"""Lossless text/table representation of an EGRUL/EGRIP PDF extract."""

import io
import re

import pdfplumber

from .transport import SourceError


def clean(value) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def parse_pdf(content: bytes) -> dict:
    pages, fields = [], []
    section = None
    subsection = None
    item_number = None
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                text = page.extract_text(x_tolerance=2) or ""
                tables = page.extract_tables()
                pages.append({"number": page_no, "text": text, "tables": tables})
                for table in tables:
                    for row in table:
                        cells = [clean(cell) for cell in row]
                        # Identifier boxes in the header are also detected as a
                        # table (10-15 columns); they are not numbered fields.
                        if len(cells) != 3:
                            continue
                        number, label, value = cells[0], cells[1], " ".join(cells[2:]).strip()
                        if cells[:3] == ["1", "2", "3"] or "Наименование показателя" in label:
                            continue
                        if number.isdigit() and label:
                            fields.append({"number": int(number), "label": label, "value": value,
                                           "section": section, "subsection": subsection, "item_number": item_number,
                                           "page_start": page_no, "page_end": page_no})
                        elif number and not label and not value:
                            # Spanning section title (including repeated party headings).
                            if number.isdigit():
                                item_number = int(number)
                                subsection = None
                            else:
                                section = number
                                subsection = None
                                item_number = None
                        elif not number and label and not value and (
                            row[2] is None or label.startswith(("Сведения о", "(ОКВЭД"))
                        ):
                            # A subheading spanning the label/value columns is
                            # NOT a continuation of the previous numbered field.
                            subsection = label
                        elif not number and (label or value) and fields:
                            previous = fields[-1]
                            if (previous["section"], previous["subsection"], previous["item_number"]) == (section, subsection, item_number):
                                previous["label"] = clean(previous["label"] + " " + label)
                                previous["value"] = clean(previous["value"] + " " + value)
                                previous["page_end"] = page_no
    except Exception as exc:
        raise SourceError("pdf_parse_error", f"Не удалось разобрать PDF: {type(exc).__name__}: {exc}") from exc
    full_text = "\n\n".join(page["text"] for page in pages)
    if not full_text.strip():
        raise SourceError("pdf_no_text", "В PDF нет извлекаемого текста; OCR в парсер не включён")
    match = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*№", full_text)
    warnings = []
    if not fields:
        warnings.append("Не распознана таблица показателей; данные сохранены в pages[].text и tables")
    elif [field["number"] for field in fields] != list(range(1, len(fields) + 1)):
        warnings.append("Нарушена последовательность номеров показателей; проверьте исходные tables и PDF")
    return {"page_count": len(pages), "formed_on": match.group(1) if match else None,
            "fields": fields, "pages": pages, "warnings": warnings}


def first_field(extract: dict | None, label: str):
    if extract:
        for field in extract.get("fields", []):
            if field["label"].casefold() == label.casefold():
                return field["value"]
    return None
