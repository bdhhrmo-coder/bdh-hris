"""
Biometric CSV/Excel batch import (CLAUDE.md §9). Column names/formats come
from BiometricColumnMapping.get_active() rather than being hardcoded, since
BDH's actual export format wasn't available to confirm at build time
(2026-09-27) — HR/IT can repoint this at the real column layout later via
Django admin without a code change.

"Biometric data is advisory, not authoritative" (§9): a row for an
employee/date that already has a MANUAL AttendanceRecord is skipped, not
overwritten — a prior human correction always outranks a later import.
"""

import csv
from datetime import datetime
from io import TextIOWrapper

import openpyxl

from .deductions import sync_undertime_deduction
from .models import AttendanceRecord


def _rows_from_csv(fileobj):
    text = TextIOWrapper(fileobj, encoding="utf-8-sig")
    reader = csv.DictReader(text)
    for row in reader:
        yield row


def _rows_from_xlsx(fileobj):
    wb = openpyxl.load_workbook(fileobj, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    headers = [str(h).strip() if h is not None else "" for h in next(rows)]
    for values in rows:
        if all(v is None for v in values):
            continue
        yield dict(zip(headers, values))


def import_biometric_file(fileobj, filename, mapping, uploaded_by):
    """
    Parses fileobj (CSV or XLSX, by filename extension) using `mapping`,
    upserts AttendanceRecord rows, and returns the BiometricImportBatch
    summarizing the result. Does not raise on a per-row problem — bad rows
    are counted and logged so one malformed line doesn't abort the batch.
    """
    from employees.models import Employee

    from .models import BiometricImportBatch

    is_xlsx = filename.lower().endswith((".xlsx", ".xlsm"))
    rows = _rows_from_xlsx(fileobj) if is_xlsx else _rows_from_csv(fileobj)

    batch = BiometricImportBatch.objects.create(
        original_filename=filename, mapping_used=mapping, uploaded_by=uploaded_by,
    )

    row_count = imported_count = skipped_count = error_count = 0
    error_lines = []

    for i, row in enumerate(rows, start=2):  # row 1 is the header
        row_count += 1
        try:
            raw_employee_id = str(row.get(mapping.employee_id_column, "")).strip()
            raw_date = row.get(mapping.date_column)
            raw_time_in = row.get(mapping.time_in_column)
            raw_time_out = row.get(mapping.time_out_column)

            if not raw_employee_id:
                raise ValueError("missing employee ID")

            employee = Employee.objects.get(employee_id=raw_employee_id)
            record_date = _parse_date(raw_date, mapping.date_format)
            time_in = _parse_time(raw_time_in, mapping.time_format)
            time_out = _parse_time(raw_time_out, mapping.time_format)

            existing = AttendanceRecord.objects.filter(employee=employee, date=record_date).first()
            if existing and existing.source == AttendanceRecord.SOURCE_MANUAL:
                skipped_count += 1
                continue

            if existing:
                existing.time_in = time_in
                existing.time_out = time_out
                existing.source = AttendanceRecord.SOURCE_BIOMETRIC
                existing.import_batch = batch
                existing.recorded_by = uploaded_by
                existing.save()
                record = existing
            else:
                record = AttendanceRecord.objects.create(
                    employee=employee, date=record_date, time_in=time_in, time_out=time_out,
                    source=AttendanceRecord.SOURCE_BIOMETRIC, import_batch=batch, recorded_by=uploaded_by,
                )
            sync_undertime_deduction(record, uploaded_by)
            imported_count += 1
        except Employee.DoesNotExist:
            error_count += 1
            error_lines.append(f"Row {i}: no employee with ID '{raw_employee_id}'")
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the batch
            error_count += 1
            error_lines.append(f"Row {i}: {exc}")

    batch.row_count = row_count
    batch.imported_count = imported_count
    batch.skipped_count = skipped_count
    batch.error_count = error_count
    batch.error_log = "\n".join(error_lines)
    batch.save()
    return batch


def _parse_date(value, date_format):
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day"):
        return value.date() if hasattr(value, "hour") else value  # datetime -> date, or already a date
    return datetime.strptime(str(value).strip(), date_format).date()


def _parse_time(value, time_format):
    if value in (None, ""):
        return None
    if hasattr(value, "hour") and not hasattr(value, "year"):
        return value  # already a datetime.time (openpyxl gives native types)
    if hasattr(value, "time"):
        return value.time()
    return datetime.strptime(str(value).strip(), time_format).time()
