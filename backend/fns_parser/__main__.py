import argparse
import json
from pathlib import Path
import sys

from .service import parse_company
from .compact import compact_result


def main(argv=None) -> int:
    # Configure before argparse: --help also contains Russian text and may run
    # through Windows pipes whose default encoding is cp1251.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Публичные данные ФНС по ИНН/ОГРН/ОГРНИП → JSON")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("identifier", nargs="?", help="ИНН (10/12 цифр), ОГРН (13) или ОГРНИП (15)")
    source.add_argument("--from-json", type=Path, help="Сократить сохранённый полный JSON без запросов к ФНС")
    parser.add_argument("--full", action="store_true", help="Вернуть полный JSON вместо краткой карточки")
    parser.add_argument("--basic", action="store_true", help="Только ЕГРЮЛ/ЕГРИП и Прозрачный бизнес, без дополнительных источников")
    parser.add_argument("--max-pages", type=int, default=3, help="Лимит страниц поиска ГИР БО: 1–10 (по умолчанию 3)")
    parser.add_argument("--output", "-o", type=Path, help="Сохранить JSON в файл вместо stdout")
    parser.add_argument("--download-dir", type=Path, default=Path("data"), help="Каталог PDF-выписок (по умолчанию data)")
    parser.add_argument("--timeout", type=float, default=20.0, help="Таймаут HTTP-запроса, секунды")
    parser.add_argument("--wait-timeout", type=float, default=60.0, help="Время ожидания формирования каждого результата")
    args = parser.parse_args(argv)
    if args.from_json and args.full:
        parser.error("--full нельзя сочетать с --from-json: файл уже содержит полный ответ")
    try:
        if args.from_json:
            result = compact_result(json.loads(args.from_json.read_text(encoding="utf-8-sig")))
        else:
            result = parse_company(args.identifier, output_dir=args.download_dir, full=args.full,
                                   timeout=args.timeout, wait_timeout=args.wait_timeout,
                                   enrich=not args.basic, max_pages=args.max_pages)
        code = {"ok": 0, "partial": 2, "error": 1, "not_found": 3}[result["status"]]
    except ValueError as exc:
        error_code = "invalid_json_input" if args.from_json else "invalid_identifier_or_options"
        result, code = {"status": "error", "error": {"code": error_code, "message": str(exc)}}, 1
    except OSError as exc:
        result, code = {"status": "error", "error": {"code": "file_error", "message": str(exc)}}, 1
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(json.dumps({"status": "error", "error": {"code": "file_error", "message": str(exc)}}, ensure_ascii=False))
            return 1
    else:
        print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
