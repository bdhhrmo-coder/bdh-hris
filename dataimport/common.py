"""Small helpers shared by both imports: reading the uploaded workbook and
turning Excel cell values into clean Python values."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d-%b-%Y", "%B %d, %Y", "%b %d, %Y")


class SheetError(Exception):
    """The file itself can't be used (wrong file, missing sheet/columns)."""


def read_sheet(uploaded_file, sheet_name, columns):
    """Return a list of (excel_row_number, {key: raw_value}) for every non-
    blank row. `columns` is a list of (key, header_text). Headers are
    matched ignoring case, spaces and the '*' that marks required ones."""
    try:
        wb = openpyxl.load_workbook(uploaded_file, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001 - any unreadable file gets the same plain message
        raise SheetError("This file could not be opened. Please upload the .xlsx template.") from exc
    if sheet_name not in wb.sheetnames:
        raise SheetError(f'The sheet "{sheet_name}" was not found. Please use the downloaded template.')
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise SheetError(f'The sheet "{sheet_name}" is empty.')

    def norm(text):
        return str(text or "").replace("*", "").strip().lower()

    header = [norm(h) for h in rows[0]]
    positions = {}
    for key, title in columns:
        if norm(title) not in header:
            raise SheetError(f'Column "{title}" is missing. Please use the downloaded template.')
        positions[key] = header.index(norm(title))

    out = []
    for offset, values in enumerate(rows[1:], start=2):
        record = {key: (values[i] if i < len(values) else None) for key, i in positions.items()}
        if all(v is None or str(v).strip() == "" for v in record.values()):
            continue
        out.append((offset, record))
    return out


def text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # e.g. a number typed into a text column: 12.0 -> "12"
    return str(value).strip()


def parse_date(value):
    """Return (date, error)."""
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    raw = text(value)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date(), None
        except ValueError:
            pass
    return None, f'"{raw}" is not a date (use YYYY-MM-DD, e.g. 2026-10-31)'


def parse_decimal(value):
    """Return (Decimal, error)."""
    if value in (None, ""):
        return None, None
    try:
        return Decimal(text(value)).quantize(Decimal("0.001")), None
    except InvalidOperation:
        return None, f'"{text(value)}" is not a number'


def split_list(value):
    """Semicolon-separated list. Not commas: a section name contains one
    ("Procurement, Property and Supply Section")."""
    return [part.strip() for part in text(value).split(";") if part.strip()]
