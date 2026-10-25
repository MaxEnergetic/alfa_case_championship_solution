"""Resumable parser collection and CSV/one-hot export for a company cohort.

python -m fns_parser.batch_training run
python -m fns_parser.batch_training export --output output/scoring_training
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
from threading import RLock

from .service import parse_company
from .training import (EXTRA_COLUMNS, atomic_json, compact_document, export_tables, load_encoder,
                       normalize_request, project, read_template)
from .transport import SourceError

BLOCKED = {"captcha_required", "rate_limited", "access_restricted", "captcha_timeout", "captcha_skipped"}
RANK = {"ok": 4, "partial": 3, "not_found": 2, "error": 1}


def now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


@contextmanager
def run_lock(output):
    """OS lock is released on crash; no stale PID file needs manual removal."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".batch.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("В этом каталоге уже работает пакетный сборщик") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_requests(path, limit=None):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as source:
            values = list(csv.DictReader(source))
    elif path.suffix.lower() == ".jsonl":
        values = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        values = read_json(path)
    if not isinstance(values, list) or not values:
        raise ValueError("Входной файл должен содержать непустой список компаний")
    unique, legal_inns = {}, {}
    for value in values:
        request = normalize_request(value)
        key = request["identifier"]
        previous = unique.get(key)
        if previous and previous["inn"] and request["inn"] and previous["inn"] != request["inn"]:
            raise ValueError("Один ОГРН сопоставлен разным ИНН")
        if request["inn"] and len(request["inn"]) == 10:
            prior_key = legal_inns.get(request["inn"])
            if prior_key and prior_key != key:
                raise ValueError("Повтор ИНН юрлица с разными запросами: используйте одну пару ИНН/ОГРН")
            legal_inns[request["inn"]] = key
        unique[key] = {**request, "inn": request["inn"] or (previous or {}).get("inn")}
    return list(unique.values())[:limit]


def errors(document):
    """Look only in source status records, never execute or interpret source text."""
    statuses = list(document.get("sources", {}).values())
    for entity in document.get("entities", []):
        statuses += [entity.get("egrul", {}), entity.get("transparent_business", {})]
        statuses += list(entity.get("public_sources", {}).values())
        statuses += entity.get("issues", [])
    return [status["error"] for status in statuses if isinstance(status, dict) and isinstance(status.get("error"), dict)]


def failure(request, exc):
    error = exc.as_dict() if isinstance(exc, SourceError) else {"code": "parser_exception", "message": str(exc)[:1200]}
    return {"schema_version": "1.0", "query": request["identifier"], "status": "error", "finished_at": now(),
            "sources": {"parser": {"status": "error", "error": error}}, "entities": []}


def result_path(output, record):
    path = (output / record["selected_file"]).resolve()
    if not path.is_relative_to((output / "raw").resolve()):
        raise ValueError("Некорректный путь к сохранённому результату")
    return path


def collect(requests, output, *, delay=1.0, attempts=2, timeout=20.0, wait_timeout=60.0,
            retry_failed=False, refresh=False, parser_func=None, sleep=time.sleep, fast=False,
            enrich=True, captcha_queue=None, captcha_timeout=1800):
    parser_func = parser_func or parse_company
    output = Path(output)
    cohort_path = output / "input.json"
    if cohort_path.exists() and read_json(cohort_path) != requests:
        raise ValueError("Состав компаний отличается от сохранённого запуска; укажите другой --output")
    atomic_json(cohort_path, requests)
    run = {"started_at": now(), "cohort_size": len(requests), "attempted_companies": 0,
           "cached_companies": 0, "interrupted": False, "stopped_on": None, "fast": fast,
           "girbo_enabled": enrich}
    previous_call = False
    progress_lock = RLock()
    live = {"pid": os.getpid(), "cohort_size": len(requests)}

    def progress(**changes):
        with progress_lock:
            live.update(changes, updated_at=now())
            atomic_json(output / "live.json", live)

    if captcha_queue is not None:
        from .captcha_queue import QueueCaptchaHandler
        captcha_handler = QueueCaptchaHandler(captcha_queue, timeout=captcha_timeout, progress=progress)
    else:
        captcha_handler = None
    try:
        for position, request in enumerate(requests, 1):
            key = request["identifier"]
            record_file = output / "records" / (key + ".json")
            record = read_json(record_file) if record_file.exists() else None
            if record and record["request"] != request:
                raise ValueError("Сохранённый запрос не совпадает с текущими идентификаторами")
            if record and not refresh and not (retry_failed and record.get("last_status") not in {"ok", "not_found"}):
                if not result_path(output, record).is_file():
                    raise ValueError("Отсутствует JSON результата: " + key)
                run["cached_companies"] += 1
                continue
            run["attempted_companies"] += 1
            previous_result = read_json(result_path(output, record)) if fast and record and not refresh else None
            for attempt in range(attempts):
                if previous_call:
                    sleep(delay if attempt == 0 else max(delay, 2 ** attempt))
                previous_call = True
                sequence = (record or {}).get("attempt_count", 0) + 1
                # A crash after writing raw data must not overwrite the orphaned attempt.
                raw_file = output / "raw" / f"{key}_{sequence:04d}.json"
                while raw_file.exists():
                    sequence += 1
                    raw_file = output / "raw" / f"{key}_{sequence:04d}.json"
                try:
                    options = {"parallel_sources": True, "previous_result": previous_result} if fast else {}
                    if captcha_handler:
                        options["captcha_handler"] = captcha_handler
                    progress(state="running", identifier=key, position=position, captcha_id=None, source=None)
                    started = time.monotonic()
                    result = parser_func(key, full=True, enrich=enrich, output_dir=output / "extracts",
                                         timeout=timeout, wait_timeout=wait_timeout, **options)
                    projected, _ = project(result, request)
                    if result.get("status") not in RANK:
                        raise ValueError("Неизвестный статус парсера")
                except (SourceError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
                    result = failure(request, exc)
                    projected, _ = project(result, request)
                atomic_json(raw_file, result)
                status = result["status"]
                quality = [RANK[status], sum(value is not None for value in projected.values())]
                selected = record if record and record.get("selected_quality", [0, 0]) > quality else {
                    "selected_file": raw_file.relative_to(output).as_posix(),
                    "selected_status": status, "selected_quality": quality,
                }
                record = {**selected, "request": request, "attempt_count": sequence,
                          "last_status": status, "last_file": raw_file.relative_to(output).as_posix(),
                          "last_errors": errors(result), "updated_at": now(),
                          "duration_seconds": round(time.monotonic() - started, 3)}
                atomic_json(record_file, record)
                if fast:
                    previous_result = result
                print(f"[{position}/{len(requests)}] {key}: {status}; attempt {sequence}", flush=True)
                blocked = next((error for error in record["last_errors"] if error.get("code") in BLOCKED), None)
                if blocked:
                    run["stopped_on"] = {"identifier": key, **blocked}
                    break
                if status in {"ok", "not_found"}:
                    break
            if run["stopped_on"]:
                break
    except KeyboardInterrupt:
        run["interrupted"] = True
    finally:
        run["finished_at"] = now()
        atomic_json(output / "run.json", run)
        completed = run["attempted_companies"] + run["cached_companies"] == len(requests)
        progress(state="finished" if completed and not run["stopped_on"] and not run["interrupted"] else "stopped", captcha_id=None)
    return run


def export_run(output, *, template=None, encoder_schema=None):
    output = Path(output)
    requests = read_json(output / "input.json")
    rows, origins, last_statuses, selected_statuses, conversion_errors = [], [], Counter(), Counter(), []
    for request in requests:
        path = output / "records" / (request["identifier"] + ".json")
        record = read_json(path) if path.exists() else None
        document = read_json(result_path(output, record)) if record else None
        source_file = record["selected_file"] if record else None
        try:
            row, origin = project(document, request, source_file)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            row, origin = project(None, request, source_file)
            row["meta_parser_status"] = "conversion_error"
            origin["conversion_error"] = str(exc)
            conversion_errors.append({"identifier": request["identifier"], "error": str(exc)})
        row["meta_last_attempt_status"] = record["last_status"] if record else "not_processed"
        last_statuses[row["meta_last_attempt_status"]] += 1
        selected_statuses[row["meta_parser_status"]] += 1
        rows.append(row)
        origins.append(origin)
    report = export_tables(rows, origins, output, template=template, encoder_schema=encoder_schema)
    report.update(created_at=now(), selected_statuses=dict(selected_statuses), last_attempt_statuses=dict(last_statuses),
                  conversion_errors=conversion_errors, source_mode="parser_batch",
                  loan_outcomes_collected=False)
    atomic_json(output / "report.json", report)
    return report


def export_files(paths, output, *, template=None, encoder_schema=None):
    """Offline conversion of explicit parser JSON files; no source requests."""
    rows, origins, seen = [], [], set()
    for path in paths:
        document = read_json(path)
        compact = compact_document(document)
        entities = compact["entities"]
        requests = [normalize_request({"inn": e.get("inn"), "ogrn": e.get("ogrn")}) for e in entities]
        if not requests:
            requests = [normalize_request(compact.get("query"))]
        for request in requests:
            if request["identifier"] in seen:
                raise ValueError("Несколько JSON одной регистрации: выберите нужный срез")
            seen.add(request["identifier"])
            row, origin = project(document, request, str(Path(path).resolve()))
            rows.append(row)
            origins.append(origin)
    report = export_tables(rows, origins, output, template=template, encoder_schema=encoder_schema)
    report.update(created_at=now(), source_mode="existing_parser_json", loan_outcomes_collected=False)
    atomic_json(Path(output) / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Собрать все компании и экспортировать CSV")
    export = subcommands.add_parser("export", help="Пересобрать CSV без запросов к источникам")
    for command in (run, export):
        command.add_argument("--output", type=Path, default=Path("output/scoring_training"))
        command.add_argument("--template", type=Path, help="CSV с исходными 65 колонками; используется только заголовок")
        command.add_argument("--encoder-schema", type=Path, help="Зафиксированная схема категорий для validation/inference")
    run.add_argument("--input", type=Path, default=Path("output/scoring_pool_10000/identifiers.json"))
    run.add_argument("--limit", type=int, help="Число компаний для пробного запуска в отдельном каталоге")
    run.add_argument("--delay", type=float, default=1.0, help="Пауза между компаниями в секундах")
    run.add_argument("--attempts", type=int, default=2, help="Попытки на компанию за один запуск")
    run.add_argument("--timeout", type=float, default=20.0)
    run.add_argument("--wait-timeout", type=float, default=60.0)
    run.add_argument("--retry-failed", action="store_true", help="Повторить partial/error; успешные использовать из кеша")
    run.add_argument("--refresh", action="store_true", help="Снова запросить все компании, сохранив предыдущие JSON")
    run.add_argument("--fast", action="store_true", help="Параллельные независимые источники и повтор только неудачных источников")
    run.add_argument("--no-girbo", action="store_true", help="Добор полей CSV: ЕГРЮЛ и ПБ; без ГИР БО, чьих показателей нет в шаблоне")
    run.add_argument("--manual-captcha", action="store_true", help="Передавать капчи ФНС в очередь FastAPI /captcha/batch")
    run.add_argument("--captcha-db", type=Path, help="Общая SQLite-очередь с FastAPI (FNS_CAPTCHA_DB или data/captcha.sqlite3)")
    run.add_argument("--captcha-timeout", type=float, default=1800, help="Время ожидания человека на каждую картинку, секунд")
    export.add_argument("--json", nargs="+", type=Path, help="Конвертировать конкретные полные/краткие JSON вместо кеша запуска")
    args = parser.parse_args(argv)
    try:
        columns = read_template(args.template) + EXTRA_COLUMNS
        if args.encoder_schema:
            load_encoder(args.encoder_schema, columns)  # Validate before expensive collection.
        with run_lock(args.output):
            if args.command == "run":
                if args.limit is not None and args.limit <= 0:
                    raise ValueError("--limit должен быть положительным")
                if args.attempts < 1 or args.attempts > 5:
                    raise ValueError("--attempts должен быть от 1 до 5")
                if not math.isfinite(args.delay) or args.delay < 0:
                    raise ValueError("--delay должен быть конечным неотрицательным числом")
                if any(not math.isfinite(v) or v <= 0 for v in (args.timeout, args.wait_timeout)):
                    raise ValueError("Таймауты должны быть конечными положительными числами")
                if not math.isfinite(args.captcha_timeout) or not 1 <= args.captcha_timeout <= 86400:
                    raise ValueError("--captcha-timeout должен быть от 1 до 86400 секунд")
                captcha_queue = None
                if args.manual_captcha:
                    from .captcha_queue import CaptchaQueue
                    captcha_queue = CaptchaQueue(args.captcha_db)
                requests = load_requests(args.input, args.limit)
                run_info = collect(requests, args.output, delay=args.delay, attempts=args.attempts,
                                   timeout=args.timeout, wait_timeout=args.wait_timeout,
                                   retry_failed=args.retry_failed, refresh=args.refresh, fast=args.fast,
                                   enrich=not args.no_girbo, captcha_queue=captcha_queue, captcha_timeout=args.captcha_timeout)
                report = export_run(args.output, template=args.template, encoder_schema=args.encoder_schema)
                code = 130 if run_info["interrupted"] else 2 if run_info["stopped_on"] or report["conversion_errors"] or any(
                    status != "ok" for status in report["last_attempt_statuses"]) else 0
            elif args.json:
                report = export_files(args.json, args.output, template=args.template, encoder_schema=args.encoder_schema)
                code = 0
            else:
                report = export_run(args.output, template=args.template, encoder_schema=args.encoder_schema)
                code = 2 if report["conversion_errors"] else 0
        print(json.dumps({"rows": report["rows"], "labelled_rows": report["labelled_rows"],
                          "output": str(args.output.resolve()), "statuses": report.get("last_attempt_statuses")}, ensure_ascii=False))
        return code
    except (ValueError, OSError, KeyError) as exc:
        print("Ошибка: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
