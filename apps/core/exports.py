"""CSV / Excel responses for report tables. Reused by every report."""

import csv
from io import BytesIO

from django.http import HttpResponse
from openpyxl import Workbook


def _plain(v):
    return "" if v is None else (str(v) if not isinstance(v, (int, float, str)) else v)


def csv_response(filename, headers, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    w = csv.writer(resp)
    w.writerow(headers)
    for r in rows:
        w.writerow([_plain(v) for v in r])
    return resp


def xlsx_response(filename, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(list(headers))
    for r in rows:
        ws.append([float(v) if hasattr(v, "as_tuple") else v for v in r])  # Excel numbers; source stays Decimal
    buf = BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(),
                        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return resp


def export_response(fmt, filename, headers, rows):
    return xlsx_response(filename, headers, rows) if fmt == "xlsx" else csv_response(filename, headers, rows)
