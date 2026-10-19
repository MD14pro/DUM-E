"""
SQLite data layer for the library station with User Registration, Penalty Engine,
Multi-Copy Inventory Management, Admin Controls, and Real-Time Auto-Excel / CSV Sync
(Users.xlsx, library_logs.xlsx, and available_books.xlsx).
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

DB_PATH = os.environ.get(
    'LIBRARY_DB', os.path.join(os.path.dirname(__file__), 'library.db'))
EXCEL_PATH = os.environ.get(
    'LIBRARY_EXCEL_LOG', os.path.join(os.path.dirname(__file__), 'library_logs.xlsx'))
USERS_EXCEL_PATH = os.environ.get(
    'LIBRARY_USERS_EXCEL', os.path.join(os.path.dirname(__file__), 'Users.xlsx'))
AVAILABLE_BOOKS_EXCEL = os.environ.get(
    'LIBRARY_AVAILABLE_BOOKS_EXCEL', os.path.join(os.path.dirname(__file__), 'available_books.xlsx'))

LOAN_DAYS = 14
OVERTIME_PENALTY_PER_DAY = 1.0  # ₹1 per day after 14 days
CURRENT_DB_RESET_TOKEN = 'v3.5_multi_copy_admin_loans_sync'

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    year      TEXT NOT NULL DEFAULT '4th',
    branch    TEXT NOT NULL DEFAULT 'ENTC',
    penalty   REAL NOT NULL DEFAULT 0.0,
    active    INTEGER NOT NULL DEFAULT 1,
    max_books INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS books (
    code             TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    author           TEXT,
    shelf            TEXT NOT NULL DEFAULT 'Cupboard_1',
    status           TEXT NOT NULL DEFAULT 'available',
    issued_to        TEXT,
    issued_at        TEXT,
    due_at           TEXT,
    times_used       INTEGER NOT NULL DEFAULT 0,
    total_copies     INTEGER NOT NULL DEFAULT 3,
    available_copies INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS loans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    book_code  TEXT NOT NULL,
    member_id  TEXT NOT NULL,
    issued_at  TEXT NOT NULL,
    due_at     TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS transactions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    book_code TEXT,
    member_id TEXT,
    action    TEXT NOT NULL,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS missions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    kind       TEXT NOT NULL,
    book_code  TEXT,
    member_id  TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

AUTHORS = {
    'AC':   'Sedra & Smith',
    'EDC':  'Boylestad & Nashelsky',
    'NT':   'Van Valkenburg',
    'ADC':  'Simon Haykin',
    'CS':   'Norman Nise',
    'FOC':  'Gerd Keiser',
    'RASC': 'Merrill Skolnik',
    'ES':   'Raj Kamal',
    'IOT':  'Arshdeep Bahga',
    'CN':   'Andrew S. Tanenbaum',
}

DEFAULT_MEMBERS = [
    ('S2500023', 'Muddassir Sheikh', '4th', 'ENTC', 0.0, 1, 3),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _ensure_columns(c: sqlite3.Connection):
    """Ensure existing tables have all required multi-copy and loan columns."""
    try:
        cur = c.cursor()
        cur.execute("PRAGMA table_info(members)")
        cols = [row[1] for row in cur.fetchall()]
        if 'year' not in cols:
            cur.execute("ALTER TABLE members ADD COLUMN year TEXT NOT NULL DEFAULT '4th'")
        if 'penalty' not in cols:
            cur.execute("ALTER TABLE members ADD COLUMN penalty REAL NOT NULL DEFAULT 0.0")

        cur.execute("PRAGMA table_info(books)")
        b_cols = [row[1] for row in cur.fetchall()]
        if 'total_copies' not in b_cols:
            cur.execute("ALTER TABLE books ADD COLUMN total_copies INTEGER NOT NULL DEFAULT 3")
        if 'available_copies' not in b_cols:
            cur.execute("ALTER TABLE books ADD COLUMN available_copies INTEGER NOT NULL DEFAULT 3")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS loans (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                book_code  TEXT NOT NULL,
                member_id  TEXT NOT NULL,
                issued_at  TEXT NOT NULL,
                due_at     TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'active'
            );
        """)
        c.commit()
    except Exception:
        pass


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            try:
                _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
                _conn.row_factory = sqlite3.Row
                _conn.executescript(SCHEMA)
                _ensure_columns(_conn)
            except sqlite3.OperationalError:
                tmp_path = os.path.join('/tmp', 'dume_library.db')
                _conn = sqlite3.connect(tmp_path, check_same_thread=False)
                _conn.row_factory = sqlite3.Row
                _conn.executescript(SCHEMA)
                _ensure_columns(_conn)
        return _conn


def q(sql: str, args: tuple = ()) -> list[dict]:
    with _lock:
        cur = conn().execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        return rows


def run(sql: str, args: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        conn().commit()
        return cur.lastrowid


def home_shelf_for_code(code: str) -> str:
    """Returns configured shelf location for book from database."""
    try:
        res = q("SELECT shelf FROM books WHERE UPPER(code) = UPPER(?)", (code.strip(),))
        if res and res[0].get('shelf'):
            return res[0]['shelf']
    except Exception:
        pass
    return 'Cupboard_2' if code.upper() in {'FOC', 'RASC', 'ES', 'IOT', 'CN'} else 'Cupboard_1'


# ---------------------------------------------------------------------------
# EXCEL / CSV DIRECTORY ENGINES (available_books.xlsx, Users.xlsx, library_logs.xlsx)
# ---------------------------------------------------------------------------
def sync_available_books_excel(excel_path: Optional[str] = None):
    """
    Export current book availability, copies count, and cupboard locations
    into available_books.xlsx with professional openpyxl formatting.
    """
    target_file = excel_path or AVAILABLE_BOOKS_EXCEL
    all_books = list_books()
    headers = [
        "Book Code", "Subject Title", "Author", "Cupboard Location",
        "Total Copies", "Available Copies", "Issued Copies", "Inventory Status", "Times Borrowed"
    ]

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Available Books"
        ws.views.sheetView[0].showGridLines = True

        NAVY_PRIMARY = "1F497D"
        MINT_GREEN   = "D1E7DD"
        SOFT_RED     = "F8D7DA"
        AMBER_WARN   = "FFF3CD"
        ALT_ROW_FILL = "F8FAFC"
        BORDER_GRAY  = "D9D9D9"

        font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        font_data = Font(name="Calibri", size=10)
        font_bold = Font(name="Calibri", size=10, bold=True)
        font_kpi_num = Font(name="Calibri", size=15, bold=True, color="1F497D")
        font_kpi_lbl = Font(name="Calibri", size=9, bold=True, color="555555")

        fill_header = PatternFill(start_color=NAVY_PRIMARY, end_color=NAVY_PRIMARY, fill_type="solid")
        fill_alt = PatternFill(start_color=ALT_ROW_FILL, end_color=ALT_ROW_FILL, fill_type="solid")
        fill_kpi_h = PatternFill(start_color="E9EEF4", end_color="E9EEF4", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="thin", color=BORDER_GRAY)
        )
        double_bottom = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="double", color=NAVY_PRIMARY)
        )

        total_titles = len(all_books)
        total_copies_sum = sum(int(b.get('total_copies') or 1) for b in all_books)
        avail_copies_sum = sum(int(b.get('available_copies') if b.get('available_copies') is not None else 0) for b in all_books)
        issued_copies_sum = max(0, total_copies_sum - avail_copies_sum)

        ws.merge_cells("A1:I1")
        t = ws["A1"]
        t.value = "DUM-E Autonomous Library Station — Available Books & Inventory Register"
        t.font = font_title
        t.fill = fill_header
        t.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 28

        kpis = [
            ("A3:B3", "A4:B4", "TOTAL SUBJECT TITLES", total_titles, "#,##0"),
            ("C3:D3", "C4:D4", "TOTAL PHYSICAL COPIES", total_copies_sum, "#,##0"),
            ("E3:F3", "E4:F4", "AVAILABLE ON SHELVES", avail_copies_sum, "#,##0"),
            ("G3:H3", "G4:H4", "CURRENTLY ISSUED", issued_copies_sum, "#,##0"),
        ]

        for tr, br, lbl, val, num_fmt in kpis:
            ws.merge_cells(tr)
            c1 = ws[tr.split(":")[0]]
            c1.value, c1.font, c1.fill = lbl, font_kpi_lbl, fill_kpi_h
            c1.alignment = Alignment(horizontal="center", vertical="center")

            ws.merge_cells(br)
            c2 = ws[br.split(":")[0]]
            c2.value, c2.font = val, font_kpi_num
            c2.alignment = Alignment(horizontal="center", vertical="center")
            c2.number_format = num_fmt

        ws.row_dimensions[3].height = 18
        ws.row_dimensions[4].height = 26

        for c_idx, h_text in enumerate(headers, start=1):
            c = ws.cell(row=6, column=c_idx, value=h_text)
            c.font, c.fill, c.border = font_header, fill_header, thin_border
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[6].height = 22

        r_idx = 7
        for b in all_books:
            code = b['code']
            title = b['title']
            author = b['author'] or 'N/A'
            shelf = b['shelf']
            tot = int(b.get('total_copies') or 1)
            avail = int(b.get('available_copies') if b.get('available_copies') is not None else (1 if b['status'] == 'available' else 0))
            iss = max(0, tot - avail)
            times = int(b.get('times_used') or 0)

            if avail > 1:
                status_label = "In Stock"
            elif avail == 1:
                status_label = "Limited (1 Left)"
            else:
                status_label = "Out of Stock"

            vals = [code, title, author, shelf, tot, avail, iss, status_label, times]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                cell.font, cell.border = font_data, thin_border
                if c_idx in (1, 4, 8):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx in (5, 6, 7, 9):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                if c_idx == 8:
                    if status_label == "In Stock":
                        cell.fill = PatternFill(start_color=MINT_GREEN, end_color=MINT_GREEN, fill_type="solid")
                    elif status_label.startswith("Limited"):
                        cell.fill = PatternFill(start_color=AMBER_WARN, end_color=AMBER_WARN, fill_type="solid")
                    else:
                        cell.fill = PatternFill(start_color=SOFT_RED, end_color=SOFT_RED, fill_type="solid")
                elif r_idx % 2 == 0:
                    cell.fill = fill_alt
            r_idx += 1

        ws.cell(row=r_idx, column=1, value="Total Copies").font = font_bold
        if r_idx > 7:
            ws.cell(row=r_idx, column=5, value=f"=SUM(E7:E{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=5).number_format = "#,##0"
            ws.cell(row=r_idx, column=6, value=f"=SUM(F7:F{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=6).number_format = "#,##0"
            ws.cell(row=r_idx, column=7, value=f"=SUM(G7:G{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=7).number_format = "#,##0"
            ws.cell(row=r_idx, column=9, value=f"=SUM(I7:I{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=9).number_format = "#,##0"
        for c_idx in range(1, 10):
            ws.cell(row=r_idx, column=c_idx).border = double_bottom

        ws.freeze_panes = "A7"

        merged_multi_col = {
            cell.coordinate
            for rng in ws.merged_cells.ranges
            if rng.min_col != rng.max_col
            for row in ws[rng.coord]
            for cell in row
        }
        for col in ws.columns:
            max_len = 0
            for cell in col:
                if cell.row in (1, 3, 4) or cell.coordinate in merged_multi_col:
                    continue
                v = str(cell.value or '')
                if not v.startswith('='):
                    max_len = max(max_len, len(v))
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 5, 14), 42)

        os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
        wb.save(target_file)
    except ImportError:
        try:
            csv_file = os.path.splitext(target_file)[0] + '.csv'
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(headers)
                for b in all_books:
                    tot = int(b.get('total_copies') or 1)
                    avail = int(b.get('available_copies') if b.get('available_copies') is not None else 0)
                    w.writerow([
                        b['code'], b['title'], b.get('author') or 'N/A', b['shelf'],
                        tot, avail, max(0, tot - avail),
                        "In Stock" if avail > 1 else ("Limited" if avail == 1 else "Out of Stock"),
                        b.get('times_used', 0)
                    ])
        except Exception:
            pass
    except Exception:
        pass


def sync_users_excel(excel_path: Optional[str] = None):
    """Sync all registered students, their penalty, year, branch, and active loans to Users.xlsx (or Users.csv fallback)."""
    target_file = excel_path or USERS_EXCEL_PATH
    members_list = list_members()
    headers = ["I-Card Barcode ID", "Student Name", "Academic Year", "Engineering Branch", "Total Penalty (INR)", "Current Open Loans", "Max Loan Quota", "Account Status"]

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Registered Students"
        ws.views.sheetView[0].showGridLines = True

        NAVY_PRIMARY = "1F497D"
        MINT_GREEN   = "D1E7DD"
        SOFT_RED     = "F8D7DA"
        ALT_ROW_FILL = "F8FAFC"
        BORDER_GRAY  = "D9D9D9"

        font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        font_data = Font(name="Calibri", size=10)
        font_bold = Font(name="Calibri", size=10, bold=True)
        font_kpi_num = Font(name="Calibri", size=15, bold=True, color="1F497D")
        font_kpi_lbl = Font(name="Calibri", size=9, bold=True, color="555555")

        fill_header = PatternFill(start_color=NAVY_PRIMARY, end_color=NAVY_PRIMARY, fill_type="solid")
        fill_alt = PatternFill(start_color=ALT_ROW_FILL, end_color=ALT_ROW_FILL, fill_type="solid")
        fill_kpi_h = PatternFill(start_color="E9EEF4", end_color="E9EEF4", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="thin", color=BORDER_GRAY)
        )
        double_bottom = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="double", color=NAVY_PRIMARY)
        )

        total_m = len(members_list)
        active_m = sum(1 for m in members_list if m['active'])
        total_pen = sum(m.get('penalty', 0.0) for m in members_list)
        all_books = list_books()
        total_open_loans = sum(1 for b in all_books if int(b.get('available_copies') or 0) < int(b.get('total_copies') or 1))

        ws.merge_cells("A1:H1")
        t = ws["A1"]
        t.value = "DUM-E Autonomous Library Station — Registered Students & Penalty Directory"
        t.font = font_title
        t.fill = fill_header
        t.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 28

        kpis = [
            ("A3:B3", "A4:B4", "REGISTERED STUDENTS", total_m, "#,##0"),
            ("C3:D3", "C4:D4", "ACTIVE MEMBERS", active_m, "#,##0"),
            ("E3:F3", "E4:F4", "TOTAL PENALTY COLLECTIBLE", total_pen, "₹#,##0.00"),
            ("G3:H3", "G4:H4", "CURRENT OPEN LOANS", total_open_loans, "#,##0"),
        ]

        for tr, br, lbl, val, num_fmt in kpis:
            ws.merge_cells(tr)
            c1 = ws[tr.split(":")[0]]
            c1.value, c1.font, c1.fill = lbl, font_kpi_lbl, fill_kpi_h
            c1.alignment = Alignment(horizontal="center", vertical="center")

            ws.merge_cells(br)
            c2 = ws[br.split(":")[0]]
            c2.value, c2.font = val, font_kpi_num
            c2.alignment = Alignment(horizontal="center", vertical="center")
            c2.number_format = num_fmt

        ws.row_dimensions[3].height = 18
        ws.row_dimensions[4].height = 26

        for c_idx, h_text in enumerate(headers, start=1):
            c = ws.cell(row=6, column=c_idx, value=h_text)
            c.font, c.fill, c.border = font_header, fill_header, thin_border
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[6].height = 22

        r_idx = 7
        for m in members_list:
            mid = m['id']
            name = m['name']
            year = m.get('year') or '4th'
            branch = m.get('branch') or 'ENTC'
            penalty = float(m.get('penalty') or 0.0)
            loans = open_loans(mid)
            max_b = m.get('max_books', 3)
            status = 'Active' if m['active'] else 'Inactive'

            vals = [mid, name, year, branch, penalty, loans, max_b, status]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws.cell(row=r_idx, column=c_idx, value=val)
                cell.font, cell.border = font_data, thin_border
                if c_idx in (1, 3, 4, 8):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx in (6, 7):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                elif c_idx == 5:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "₹#,##0.00"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                if c_idx == 8 and val == 'Active':
                    cell.fill = PatternFill(start_color=MINT_GREEN, end_color=MINT_GREEN, fill_type="solid")
                elif c_idx == 5 and penalty > 0:
                    cell.fill = PatternFill(start_color=SOFT_RED, end_color=SOFT_RED, fill_type="solid")
                elif r_idx % 2 == 0:
                    cell.fill = fill_alt
            r_idx += 1

        ws.cell(row=r_idx, column=1, value="Total Registered").font = font_bold
        if r_idx > 7:
            ws.cell(row=r_idx, column=5, value=f"=SUM(E7:E{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=5).number_format = "₹#,##0.00"
            ws.cell(row=r_idx, column=6, value=f"=SUM(F7:F{r_idx-1})").font = font_bold
            ws.cell(row=r_idx, column=6).number_format = "#,##0"
        for c_idx in range(1, 9):
            ws.cell(row=r_idx, column=c_idx).border = double_bottom

        ws.freeze_panes = "A7"

        merged_multi_col = {
            cell.coordinate
            for rng in ws.merged_cells.ranges
            if rng.min_col != rng.max_col
            for row in ws[rng.coord]
            for cell in row
        }
        for col in ws.columns:
            max_len = 0
            for cell in col:
                if cell.row in (1, 3, 4) or cell.coordinate in merged_multi_col:
                    continue
                v = str(cell.value or '')
                if not v.startswith('='):
                    max_len = max(max_len, len(v))
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 5, 14), 38)

        os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
        wb.save(target_file)
    except ImportError:
        try:
            csv_file = os.path.splitext(target_file)[0] + '.csv'
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(headers)
                for m in members_list:
                    w.writerow([
                        m['id'], m['name'], m.get('year') or '4th', m.get('branch') or 'ENTC',
                        float(m.get('penalty') or 0.0), open_loans(m['id']),
                        m.get('max_books', 3), 'Active' if m['active'] else 'Inactive'
                    ])
        except Exception:
            pass
    except Exception:
        pass


def sync_to_excel(excel_path: Optional[str] = None):
    """Real-time export of books state, transactions, and missions into library_logs.xlsx (or CSV fallback)."""
    target_file = excel_path or EXCEL_PATH
    all_books = list_books()
    headers1 = ["Book Code", "Book Title", "Author", "Physical Location", "Total Copies", "Available Copies", "Issued Copies", "Current Status", "Times Used"]

    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter

        wb = openpyxl.Workbook()

        NAVY_PRIMARY = "1F497D"
        MINT_GREEN   = "D1E7DD"
        SOFT_RED     = "F8D7DA"
        AMBER_WARN   = "FFF3CD"
        ALT_ROW_FILL = "F8FAFC"
        BORDER_GRAY  = "D9D9D9"

        font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
        font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        font_data = Font(name="Calibri", size=10)
        font_bold = Font(name="Calibri", size=10, bold=True)
        font_kpi_num = Font(name="Calibri", size=15, bold=True, color="1F497D")
        font_kpi_lbl = Font(name="Calibri", size=9, bold=True, color="555555")

        fill_header = PatternFill(start_color=NAVY_PRIMARY, end_color=NAVY_PRIMARY, fill_type="solid")
        fill_alt = PatternFill(start_color=ALT_ROW_FILL, end_color=ALT_ROW_FILL, fill_type="solid")
        fill_kpi_h = PatternFill(start_color="E9EEF4", end_color="E9EEF4", fill_type="solid")

        thin_border = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="thin", color=BORDER_GRAY)
        )
        double_bottom = Border(
            left=Side(style="thin", color=BORDER_GRAY),
            right=Side(style="thin", color=BORDER_GRAY),
            top=Side(style="thin", color=BORDER_GRAY),
            bottom=Side(style="double", color=NAVY_PRIMARY)
        )

        total_b = len(all_books)
        total_copies_sum = sum(int(b.get('total_copies') or 1) for b in all_books)
        avail_copies_sum = sum(int(b.get('available_copies') if b.get('available_copies') is not None else 0) for b in all_books)
        issued_copies_sum = max(0, total_copies_sum - avail_copies_sum)

        ws1 = wb.active
        ws1.title = "Live Books State"
        ws1.views.sheetView[0].showGridLines = True

        ws1.merge_cells("A1:I1")
        t1 = ws1["A1"]
        t1.value = "DUM-E Autonomous Library Station — Live Books & Location Sync"
        t1.font, t1.fill = font_title, fill_header
        t1.alignment = Alignment(horizontal="center", vertical="center")
        ws1.row_dimensions[1].height = 28

        kpis = [
            ("A3:B3", "A4:B4", "TOTAL TITLES", total_b),
            ("C3:D3", "C4:D4", "TOTAL COPIES", total_copies_sum),
            ("E3:F3", "E4:F4", "AVAILABLE (ON SHELF)", avail_copies_sum),
            ("G3:H3", "G4:H4", "CURRENTLY ISSUED", issued_copies_sum),
        ]
        for tr, br, lbl, val in kpis:
            ws1.merge_cells(tr)
            c1 = ws1[tr.split(":")[0]]
            c1.value, c1.font, c1.fill = lbl, font_kpi_lbl, fill_kpi_h
            c1.alignment = Alignment(horizontal="center", vertical="center")

            ws1.merge_cells(br)
            c2 = ws1[br.split(":")[0]]
            c2.value, c2.font = val, font_kpi_num
            c2.alignment = Alignment(horizontal="center", vertical="center")

        ws1.row_dimensions[3].height = 18
        ws1.row_dimensions[4].height = 26

        for c_idx, h_text in enumerate(headers1, start=1):
            c = ws1.cell(row=6, column=c_idx, value=h_text)
            c.font, c.fill, c.border = font_header, fill_header, thin_border
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws1.row_dimensions[6].height = 22

        r_idx = 7
        for b in all_books:
            tot = int(b.get('total_copies') or 1)
            avail = int(b.get('available_copies') if b.get('available_copies') is not None else (1 if b['status'] == 'available' else 0))
            iss = max(0, tot - avail)
            vals = [b['code'], b['title'], b['author'] or 'N/A', b['shelf'], tot, avail, iss, b['status'], b['times_used']]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws1.cell(row=r_idx, column=c_idx, value=val)
                cell.font, cell.border = font_data, thin_border
                if c_idx in (1, 4, 8):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_idx in (5, 6, 7, 9):
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                if c_idx == 8:
                    if val == 'available':
                        cell.fill = PatternFill(start_color=MINT_GREEN, end_color=MINT_GREEN, fill_type="solid")
                    elif val == 'issued':
                        cell.fill = PatternFill(start_color=SOFT_RED, end_color=SOFT_RED, fill_type="solid")
                    elif val == 'in_transit':
                        cell.fill = PatternFill(start_color=AMBER_WARN, end_color=AMBER_WARN, fill_type="solid")
                elif r_idx % 2 == 0:
                    cell.fill = fill_alt
            r_idx += 1

        ws1.cell(row=r_idx, column=1, value="Total Copies").font = font_bold
        if r_idx > 7:
            ws1.cell(row=r_idx, column=5, value=f"=SUM(E7:E{r_idx-1})").font = font_bold
            ws1.cell(row=r_idx, column=5).number_format = "#,##0"
            ws1.cell(row=r_idx, column=6, value=f"=SUM(F7:F{r_idx-1})").font = font_bold
            ws1.cell(row=r_idx, column=6).number_format = "#,##0"
            ws1.cell(row=r_idx, column=7, value=f"=SUM(G7:G{r_idx-1})").font = font_bold
            ws1.cell(row=r_idx, column=7).number_format = "#,##0"
            ws1.cell(row=r_idx, column=9, value=f"=SUM(I7:I{r_idx-1})").font = font_bold
            ws1.cell(row=r_idx, column=9).number_format = "#,##0"
        for c_idx in range(1, 10):
            ws1.cell(row=r_idx, column=c_idx).border = double_bottom

        ws1.freeze_panes = "A7"

        ws2 = wb.create_sheet(title="Transaction Audit Logs")
        ws2.views.sheetView[0].showGridLines = True
        ws2.merge_cells("A1:F1")
        t2 = ws2["A1"]
        t2.value = "Library Station Transaction & Event Audit Ledger"
        t2.font, t2.fill = font_title, fill_header
        t2.alignment = Alignment(horizontal="center", vertical="center")
        ws2.row_dimensions[1].height = 28

        tx_headers = ["Log ID", "UTC Timestamp", "Book Code", "Member ID", "Action Event", "Execution Note"]
        for c_idx, h_text in enumerate(tx_headers, start=1):
            c = ws2.cell(row=3, column=c_idx, value=h_text)
            c.font, c.fill, c.border = font_header, fill_header, thin_border
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws2.row_dimensions[3].height = 22

        all_tx = q("SELECT id, ts, book_code, member_id, action, note FROM transactions ORDER BY id DESC")
        r_idx = 4
        for r in all_tx:
            vals = [r['id'], r['ts'], r['book_code'] or '—', r['member_id'] or '—', r['action'], r['note'] or '']
            for c_idx, val in enumerate(vals, start=1):
                cell = ws2.cell(row=r_idx, column=c_idx, value=val)
                cell.font, cell.border = font_data, thin_border
                if c_idx == 1:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                elif c_idx in (2, 3, 4, 5):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                if r_idx % 2 == 0:
                    cell.fill = fill_alt
            r_idx += 1

        ws2.freeze_panes = "A4"

        ws3 = wb.create_sheet(title="Missions History")
        ws3.views.sheetView[0].showGridLines = True
        ws3.merge_cells("A1:G1")
        t3 = ws3["A1"]
        t3.value = "DUM-E Robot Dispatch & Mission Queue History"
        t3.font, t3.fill = font_title, fill_header
        t3.alignment = Alignment(horizontal="center", vertical="center")
        ws3.row_dimensions[1].height = 28

        mis_headers = ["Mission ID", "Created At", "Updated At", "Kind", "Book Code", "Member ID", "Status"]
        for c_idx, h_text in enumerate(mis_headers, start=1):
            c = ws3.cell(row=3, column=c_idx, value=h_text)
            c.font, c.fill, c.border = font_header, fill_header, thin_border
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws3.row_dimensions[3].height = 22

        all_mis = q("SELECT id, created_at, updated_at, kind, book_code, member_id, status FROM missions ORDER BY id DESC")
        r_idx = 4
        for r in all_mis:
            vals = [r['id'], r['created_at'], r['updated_at'], r['kind'], r['book_code'] or '—', r['member_id'] or '—', r['status']]
            for c_idx, val in enumerate(vals, start=1):
                cell = ws3.cell(row=r_idx, column=c_idx, value=val)
                cell.font, cell.border = font_data, thin_border
                if c_idx == 1:
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.number_format = "#,##0"
                elif c_idx in (2, 3, 4, 5, 6, 7):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="left", vertical="center")

                if c_idx == 7:
                    if val == 'done':
                        cell.fill = PatternFill(start_color=MINT_GREEN, end_color=MINT_GREEN, fill_type="solid")
                    elif val == 'failed':
                        cell.fill = PatternFill(start_color=SOFT_RED, end_color=SOFT_RED, fill_type="solid")
                    elif val in ('pending', 'running'):
                        cell.fill = PatternFill(start_color=AMBER_WARN, end_color=AMBER_WARN, fill_type="solid")
                elif r_idx % 2 == 0:
                    cell.fill = fill_alt
            r_idx += 1

        ws3.freeze_panes = "A4"

        for ws in [ws1, ws2, ws3]:
            merged_multi_col = {
                cell.coordinate
                for rng in ws.merged_cells.ranges
                if rng.min_col != rng.max_col
                for row in ws[rng.coord]
                for cell in row
            }
            for col in ws.columns:
                max_len = 0
                for cell in col:
                    if cell.row in (1, 3, 4) or cell.coordinate in merged_multi_col:
                        continue
                    v = str(cell.value or '')
                    if not v.startswith('='):
                        max_len = max(max_len, len(v))
                col_letter = get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = min(max(max_len + 5, 14), 42)

        os.makedirs(os.path.dirname(os.path.abspath(target_file)), exist_ok=True)
        wb.save(target_file)
    except ImportError:
        try:
            csv_file = os.path.splitext(target_file)[0] + '.csv'
            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                w.writerow(headers1)
                for b in all_books:
                    tot = int(b.get('total_copies') or 1)
                    avail = int(b.get('available_copies') if b.get('available_copies') is not None else 0)
                    w.writerow([
                        b['code'], b['title'], b['author'] or 'N/A',
                        b['shelf'], tot, avail, max(0, tot - avail),
                        b['status'], b.get('times_used', 0)
                    ])
        except Exception:
            pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
def reset_database(catalog: dict, members: Optional[list] = None):
    """
    Completely clear old transactions, missions, and issued states, recreate tables cleanly,
    place all 10 books on their home shelves (with CS in Cupboard_1), and reset student
    directory to Muddassir Sheikh (4th Year, ENTC).
    """
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None

        for ext in ('', '-wal', '-shm'):
            try:
                if os.path.exists(DB_PATH + ext):
                    os.remove(DB_PATH + ext)
            except Exception:
                pass

        connection = conn()
        try:
            connection.executescript("""
                DROP TABLE IF EXISTS transactions;
                DROP TABLE IF EXISTS missions;
                DROP TABLE IF EXISTS loans;
                DROP TABLE IF EXISTS books;
                DROP TABLE IF EXISTS members;
            """)
            connection.executescript(SCHEMA)
            _ensure_columns(connection)

            for code, meta in catalog.items():
                shelf = home_shelf_for_code(code)
                connection.execute(
                    """INSERT INTO books (code, title, author, shelf, status, issued_to, issued_at, due_at, times_used, total_copies, available_copies)
                       VALUES (?, ?, ?, ?, 'available', NULL, NULL, NULL, 0, 3, 3)""",
                    (code, meta['title'], AUTHORS.get(code, 'Unknown'), shelf)
                )

            seed_members = members or DEFAULT_MEMBERS
            for m in seed_members:
                connection.execute(
                    """INSERT INTO members (id, name, year, branch, penalty, active, max_books)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    m
                )

            connection.execute(
                "INSERT INTO kv (k, v) VALUES ('db_reset_token', ?) "
                "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (CURRENT_DB_RESET_TOKEN,)
            )
            connection.commit()
            sync_to_excel()
            sync_users_excel()
            sync_available_books_excel()
        except Exception as e:
            connection.rollback()
            raise e


def seed(catalog: dict, members: Optional[list] = None):
    """
    Initialize database. Automatically triggers a clean reset once if migrating
    to CURRENT_DB_RESET_TOKEN and always recovers any stale 'in_transit' books.
    """
    with _lock:
        connection = conn()
        _ensure_columns(connection)
        token_row = q("SELECT v FROM kv WHERE k='db_reset_token'")
        if not token_row or token_row[0]['v'] != CURRENT_DB_RESET_TOKEN:
            reset_database(catalog, members)
            return

        try:
            # Always recover any book stuck in 'in_transit' from a previous aborted run
            connection.execute(
                "UPDATE books SET status='available', issued_to=NULL, issued_at=NULL, due_at=NULL WHERE status='in_transit'"
            )
            # Recover any orphaned/aborted missions left in 'pending' or 'running'
            connection.execute(
                "UPDATE missions SET status='failed', detail='Session restarted' WHERE status IN ('pending', 'running')"
            )
            for code, meta in catalog.items():
                shelf = home_shelf_for_code(code)
                connection.execute(
                    """INSERT INTO books (code, title, author, shelf, total_copies, available_copies)
                       VALUES (?, ?, ?, ?, 3, 3)
                       ON CONFLICT(code) DO UPDATE SET
                         title=excluded.title,
                         author=excluded.author,
                         shelf=CASE WHEN books.status='available' THEN excluded.shelf ELSE books.shelf END""",
                    (code, meta['title'], AUTHORS.get(code, 'Unknown'), shelf)
                )

            seed_members = members or DEFAULT_MEMBERS
            for m in seed_members:
                connection.execute(
                    """INSERT INTO members (id, name, year, branch, penalty, active, max_books)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         name=excluded.name,
                         year=excluded.year,
                         branch=excluded.branch""",
                    m
                )
            connection.commit()
            sync_to_excel()
            sync_users_excel()
            sync_available_books_excel()
        except Exception as e:
            connection.rollback()
            raise e


# ------------------------------------------------------------------ members
def get_member(mid: str) -> Optional[dict]:
    clean_id = mid.strip().upper()
    r = q('SELECT * FROM members WHERE UPPER(id) = ?', (clean_id,))
    if not r:
        return None
    m = r[0]
    m['open_loans'] = open_loans(m['id'])
    return m


def list_members() -> list[dict]:
    members = q('SELECT * FROM members ORDER BY name')
    for m in members:
        m['open_loans'] = open_loans(m['id'])
    return members


def add_member(mid: str, name: str, year: str = '4th', branch: str = 'ENTC', max_books: int = 3) -> dict:
    """Register or update a student with Name, Year, Branch, and I-Card Barcode ID."""
    clean_id = mid.strip().upper()
    clean_name = name.strip()
    clean_year = year.strip()
    clean_branch = branch.strip()

    run("""INSERT INTO members (id, name, year, branch, penalty, active, max_books)
           VALUES (?, ?, ?, ?, 0.0, 1, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name,
             year=excluded.year,
             branch=excluded.branch""", (clean_id, clean_name, clean_year, clean_branch, max_books))

    log_tx(None, clean_id, 'member_registered', f"{clean_name} ({clean_year} Year, {clean_branch}) registered")
    sync_users_excel()
    sync_to_excel()
    return get_member(clean_id)


def open_loans(mid: str) -> int:
    """Returns number of books currently on loan to this student."""
    return q("SELECT COUNT(*) c FROM loans WHERE UPPER(member_id) = UPPER(?) AND status = 'active'",
             (mid.strip(),))[0]['c']


def list_member_loans(mid: str) -> list[dict]:
    """Returns active loans for a specific student."""
    clean_id = mid.strip().upper()
    return q("""
        SELECT l.id, l.book_code, l.issued_at, l.due_at, b.title, b.author, b.shelf
        FROM loans l
        JOIN books b ON l.book_code = b.code
        WHERE UPPER(l.member_id) = ? AND l.status = 'active'
        ORDER BY l.id DESC
    """, (clean_id,))


# -------------------------------------------------------------------- books & admin
def list_books(member_id: Optional[str] = None) -> list[dict]:
    """Returns all books, populated with copies, loan counts, and member-specific loan status."""
    rows = q('SELECT * FROM books ORDER BY code')
    clean_mid = member_id.strip().upper() if member_id else None
    user_loans_codes = set()
    if clean_mid:
        loan_rows = q("SELECT book_code FROM loans WHERE UPPER(member_id) = ? AND status = 'active'", (clean_mid,))
        user_loans_codes = {r['book_code'].upper() for r in loan_rows}

    for r in rows:
        r['total_copies'] = int(r.get('total_copies') or 1)
        r['available_copies'] = int(r.get('available_copies') if r.get('available_copies') is not None else (1 if r['status'] == 'available' else 0))
        r['issued_copies'] = max(0, r['total_copies'] - r['available_copies'])
        r['overdue'] = bool(r['due_at'] and r['status'] == 'issued' and r['due_at'] < now())
        r['user_has_loan'] = (r['code'].upper() in user_loans_codes) if clean_mid else False
    return rows


def get_book(code: str) -> Optional[dict]:
    r = q('SELECT * FROM books WHERE code = ?', (code.strip().upper(),))
    if not r:
        return None
    b = r[0]
    b['total_copies'] = int(b.get('total_copies') or 1)
    b['available_copies'] = int(b.get('available_copies') if b.get('available_copies') is not None else (1 if b['status'] == 'available' else 0))
    b['issued_copies'] = max(0, b['total_copies'] - b['available_copies'])
    return b


def add_or_update_book(code: str, title: str, author: str = '', shelf: str = 'Cupboard_1', total_copies: int = 1) -> dict:
    """Admin function: add a new book or update copies and cupboard location of an existing book."""
    code_up = code.strip().upper()
    title_clean = title.strip()
    author_clean = author.strip() or AUTHORS.get(code_up, 'Unknown')
    shelf_clean = shelf.strip() or ('Cupboard_2' if code_up in {'FOC', 'RASC', 'ES', 'IOT', 'CN'} else 'Cupboard_1')
    total_copies = max(1, int(total_copies))

    with _lock:
        existing = get_book(code_up)
        if existing:
            active_loans_count = q("SELECT COUNT(*) c FROM loans WHERE UPPER(book_code) = ? AND status = 'active'", (code_up,))[0]['c']
            new_available = max(0, total_copies - active_loans_count)
            new_status = 'available' if new_available > 0 else 'issued'
            run("""
                UPDATE books SET
                    title = ?,
                    author = ?,
                    shelf = ?,
                    total_copies = ?,
                    available_copies = ?,
                    status = ?
                WHERE code = ?
            """, (title_clean, author_clean, shelf_clean, total_copies, new_available, new_status, code_up))
            log_tx(code_up, 'librarian', 'book_updated', f"Updated {title_clean}: shelf={shelf_clean}, total_copies={total_copies}, available={new_available}")
        else:
            run("""
                INSERT INTO books (code, title, author, shelf, status, times_used, total_copies, available_copies)
                VALUES (?, ?, ?, ?, 'available', 0, ?, ?)
            """, (code_up, title_clean, author_clean, shelf_clean, total_copies, total_copies))
            log_tx(code_up, 'librarian', 'book_added', f"Added {title_clean} ({code_up}) at {shelf_clean} with {total_copies} copies")

        sync_available_books_excel()
        sync_to_excel()
        return get_book(code_up)


def set_book_copies(code: str, delta: int) -> dict:
    """Admin quick-adjust: increment/decrement total copies of a book."""
    code_up = code.strip().upper()
    b = get_book(code_up)
    if not b:
        raise LibraryError(f"Book '{code}' not found.")
    curr_total = int(b.get('total_copies') or 1)
    new_total = max(1, curr_total + delta)
    return add_or_update_book(code_up, b['title'], b.get('author') or '', b['shelf'], new_total)


def set_book_shelf(code: str, shelf: str) -> dict:
    """Admin quick-adjust: change designated cupboard location for a book."""
    code_up = code.strip().upper()
    b = get_book(code_up)
    if not b:
        raise LibraryError(f"Book '{code}' not found.")
    return add_or_update_book(code_up, b['title'], b.get('author') or '', shelf, int(b.get('total_copies') or 1))


def log_tx(book: Optional[str], member: Optional[str], action: str, note: str = ''):
    run('INSERT INTO transactions (ts, book_code, member_id, action, note) '
        'VALUES (?, ?, ?, ?, ?)', (now(), book, member, action, note))
    sync_to_excel()


def recent_transactions(limit: int = 60) -> list[dict]:
    return q('SELECT * FROM transactions ORDER BY id DESC LIMIT ?', (limit,))


# ------------------------------------------------------------------ actions & penalty
class LibraryError(Exception):
    pass


def request_issue(code: str, member_id: str) -> dict:
    """Book reserve karke robot ke liye fetch mission queue me daalta hai, aur quantity -1 karta hai."""
    code = code.strip().upper()
    book = get_book(code)
    member = get_member(member_id)
    if not book:
        raise LibraryError(f"'{code}' catalog me nahi hai.")
    if not member or not member['active']:
        raise LibraryError(f"Student ID '{member_id}' registered ya active nahi hai.")
    canonical_mid = member['id']

    avail = int(book.get('available_copies') if book.get('available_copies') is not None else (1 if book['status'] == 'available' else 0))
    if avail <= 0:
        raise LibraryError(f"'{book['title']}' ({code}) ki saari copies abhi issued hain (Out of stock).")

    if open_loans(canonical_mid) >= member['max_books']:
        raise LibraryError(f"{member['name']} ki limit ({member['max_books']} books) full hai.")

    already_has = q("SELECT COUNT(*) c FROM loans WHERE UPPER(book_code) = ? AND UPPER(member_id) = ? AND status = 'active'",
                    (code, canonical_mid))[0]['c']
    if already_has > 0:
        raise LibraryError(f"Aapke paas '{book['title']}' ({code}) ki copy already issued hai.")

    due = (datetime.now(timezone.utc) + timedelta(days=LOAN_DAYS)).isoformat(timespec='seconds')
    now_ts = now()

    # Decrement available copies by 1
    new_avail = avail - 1
    new_status = 'available' if new_avail > 0 else 'issued'

    run("""UPDATE books SET
           status = ?,
           available_copies = ?,
           issued_to = ?,
           issued_at = ?,
           due_at = ?,
           times_used = times_used + 1
           WHERE code = ?""",
        (new_status, new_avail, canonical_mid, now_ts, due, code))

    run("""INSERT INTO loans (book_code, member_id, issued_at, due_at, status)
           VALUES (?, ?, ?, ?, 'active')""",
        (code, canonical_mid, now_ts, due))

    mid = run("""INSERT INTO missions (created_at, updated_at, kind, book_code,
                 member_id, status, detail) VALUES (?, ?, 'fetch', ?, ?, 'pending', '')""",
              (now_ts, now_ts, code, canonical_mid))

    log_tx(code, canonical_mid, 'issue_requested', f'mission #{mid} (Remaining available: {new_avail})')
    sync_to_excel()
    sync_users_excel()
    sync_available_books_excel()
    return {'mission_id': mid, 'book': get_book(code), 'due_at': due}


def request_return(code: str, member_id: str) -> dict:
    """Book return queue me daalta hai sirf agar ye book is student ke paas active loan me ho."""
    code = code.strip().upper()
    book = get_book(code)
    member = get_member(member_id)
    canonical_mid = member['id'] if member else member_id.strip().upper()
    if not book:
        raise LibraryError(f"'{code}' catalog me nahi hai.")

    # Verify student actually has this book on active loan!
    user_loan = q("SELECT * FROM loans WHERE UPPER(book_code) = ? AND UPPER(member_id) = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                  (code, canonical_mid))
    if not user_loan:
        raise LibraryError(f"Aapke paas '{code}' issued nahi hai return karne ke liye.")

    mid = run("""INSERT INTO missions (created_at, updated_at, kind, book_code,
                 member_id, status, detail) VALUES (?, ?, 'return', ?, ?, 'pending', '')""",
              (now(), now(), code, canonical_mid))
    log_tx(code, canonical_mid, 'return_requested', f'mission #{mid}')
    sync_to_excel()
    return {'mission_id': mid}


def complete_mission(mid: int, ok: bool, detail: str = ''):
    """
    Handles mission completion.
    On book return: increments available copies (+1), closes active loan,
    calculates overtime penalty if applicable, and syncs available_books.xlsx.
    """
    m = q('SELECT * FROM missions WHERE id = ?', (mid,))
    if not m:
        raise LibraryError(f'Mission {mid} nahi mila.')
    m = m[0]
    run('UPDATE missions SET status=?, detail=?, updated_at=? WHERE id=?',
        ('done' if ok else 'failed', detail, now(), mid))

    code = m['book_code']
    member_id = m['member_id']
    book = get_book(code)

    if ok and m['kind'] == 'fetch':
        run("UPDATE books SET shelf='Drop_Table' WHERE code=?", (code,))
        log_tx(code, member_id, 'issued', 'robot ne table pe rakhi')
    elif ok and m['kind'] == 'return':
        target_shelf = home_shelf_for_code(code)

        # Mark loan as returned
        loan_row = q("SELECT * FROM loans WHERE UPPER(book_code) = ? AND UPPER(member_id) = ? AND status = 'active' ORDER BY id DESC LIMIT 1",
                     (code, member_id))
        penalty_amount = 0.0
        overdue_note = 'Returned on time'

        if loan_row:
            loan_id = loan_row[0]['id']
            run("UPDATE loans SET status = 'returned' WHERE id = ?", (loan_id,))
            due_at_str = loan_row[0].get('due_at')
            if due_at_str:
                try:
                    due_dt = datetime.fromisoformat(due_at_str)
                    now_dt = datetime.fromisoformat(now())
                    if now_dt > due_dt:
                        overdue_days = (now_dt - due_dt).days
                        if (now_dt - due_dt).total_seconds() > 0 and overdue_days == 0:
                            overdue_days = 1
                        penalty_amount = float(overdue_days * OVERTIME_PENALTY_PER_DAY)
                        overdue_note = f'Overdue by {overdue_days} day(s). Fine: Rs.{penalty_amount:.2f} (Rs.1/day)'
                        run("UPDATE members SET penalty = penalty + ? WHERE UPPER(id) = UPPER(?)", (penalty_amount, member_id))
                except Exception as e:
                    print(f"[PENALTY CALC WARN] Error calculating overtime fine: {e}")

        # Increment available quantity by 1
        tot = int(book.get('total_copies') or 1)
        cur_avail = int(book.get('available_copies') if book.get('available_copies') is not None else 0)
        new_avail = min(tot, cur_avail + 1)

        # Check remaining active loans for this book to update books table metadata
        remaining_loan = q("SELECT member_id, due_at, issued_at FROM loans WHERE UPPER(book_code) = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (code,))
        if remaining_loan:
            rem_mid = remaining_loan[0]['member_id']
            rem_due = remaining_loan[0]['due_at']
            rem_issued = remaining_loan[0]['issued_at']
            run("""UPDATE books SET
                   available_copies = ?,
                   status = 'available',
                   shelf = ?,
                   issued_to = ?,
                   due_at = ?,
                   issued_at = ?
                   WHERE code = ?""",
                (new_avail, target_shelf, rem_mid, rem_due, rem_issued, code))
        else:
            run("""UPDATE books SET
                   available_copies = ?,
                   status = 'available',
                   shelf = ?,
                   issued_to = NULL,
                   due_at = NULL,
                   issued_at = NULL
                   WHERE code = ?""",
                (new_avail, target_shelf, code))

        log_tx(code, member_id, 'returned', f"robot ne {target_shelf} pe rakhi. Available: {new_avail}/{tot}. {overdue_note}")
    elif not ok and m['kind'] == 'fetch':
        # Revert quantity if fetch mission failed
        tot = int(book.get('total_copies') or 1)
        cur_avail = int(book.get('available_copies') if book.get('available_copies') is not None else 0)
        new_avail = min(tot, cur_avail + 1)
        run("UPDATE books SET available_copies = ?, status = 'available' WHERE code = ?", (new_avail, code))
        run("DELETE FROM loans WHERE UPPER(book_code) = ? AND UPPER(member_id) = ? AND status = 'active' ORDER BY id DESC LIMIT 1", (code, member_id))
        log_tx(code, member_id, 'issue_failed', detail)
    else:
        log_tx(code, member_id, 'return_failed', detail)

    sync_to_excel()
    sync_users_excel()
    sync_available_books_excel()


def next_mission() -> Optional[dict]:
    r = q("SELECT * FROM missions WHERE status='pending' ORDER BY id LIMIT 1")
    return r[0] if r else None


def claim_mission(mid: int):
    run("UPDATE missions SET status='running', updated_at=? WHERE id=?", (now(), mid))
    sync_to_excel()


def list_missions(limit: int = 30) -> list[dict]:
    return q('SELECT * FROM missions ORDER BY id DESC LIMIT ?', (limit,))


# ------------------------------------------------------------------ robot kv
def set_robot_state(payload: dict):
    run("INSERT INTO kv (k, v) VALUES ('robot', ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(payload),))


def get_robot_state() -> dict:
    r = q("SELECT v FROM kv WHERE k='robot'")
    if not r:
        return {'state': 'offline', 'detail': 'Robot connected nahi hai.',
                'position': [0, 0], 'holding': None, 'progress': 0, 'log': []}
    return json.loads(r[0]['v'])


def stats() -> dict:
    b = list_books()
    members_list = list_members()
    active_member = members_list[0] if members_list else None
    issued_codes = [x['code'] for x in b if active_member and x['user_has_loan']]
    total_pen = sum(m.get('penalty', 0.0) for m in members_list)
    tot_copies = sum(int(x.get('total_copies') or 1) for x in b)
    avail_copies = sum(int(x.get('available_copies') or 0) for x in b)
    return {
        'total': len(b),
        'total_copies': tot_copies,
        'available_copies': avail_copies,
        'issued_copies': max(0, tot_copies - avail_copies),
        'available': sum(1 for x in b if int(x.get('available_copies') or 0) > 0),
        'issued': sum(1 for x in b if int(x.get('available_copies') or 0) == 0),
        'in_transit': sum(1 for x in b if x['status'] == 'in_transit'),
        'overdue': sum(1 for x in b if x['overdue']),
        'members': len(members_list),
        'total_penalty': total_pen,
        'current_member': active_member,
        'issued_books': issued_codes,
    }

# ------------------------------------------------------------------ auth session kv
def set_auth_member(member: Optional[dict]):
    if member is None:
        run("DELETE FROM kv WHERE k='auth_member'")
    else:
        run("INSERT INTO kv (k, v) VALUES ('auth_member', ?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (json.dumps(member),))


def get_auth_member() -> Optional[dict]:
    r = q("SELECT v FROM kv WHERE k='auth_member'")
    if not r or not r[0].get('v'):
        return None
    try:
        m = json.loads(r[0]['v'])
        if m and m.get('id'):
            fresh = get_member(m['id'])
            return fresh or m
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ built-in optical i-card scanner
def decode_codes_from_frame(frame) -> list[tuple[str, list[tuple[int, int]]]]:
    """Decode QR codes or 1D barcodes from a BGR image using pyzbar + OpenCV fallbacks."""
    results: list[tuple[str, list[tuple[int, int]]]] = []
    if frame is None or getattr(frame, 'size', 0) == 0:
        return results

    try:
        from pyzbar.pyzbar import decode as pyzbar_decode
        for obj in pyzbar_decode(frame):
            code = obj.data.decode('utf-8', 'ignore').strip()
            if code:
                pts = [(p.x, p.y) for p in getattr(obj, 'polygon', [])] or []
                results.append((code, pts))
    except Exception:
        pass

    if results:
        return results

    try:
        import cv2
        qr = cv2.QRCodeDetector()
        data, bbox, _ = qr.detectAndDecode(frame)
        if data and data.strip():
            pts = []
            if bbox is not None and len(bbox) > 0:
                pts = [(int(p[0]), int(p[1])) for p in bbox[0]]
            results.append((data.strip(), pts))
    except Exception:
        pass

    try:
        import cv2
        if not results and hasattr(cv2, 'barcode') and hasattr(cv2.barcode, 'BarcodeDetector'):
            bd = cv2.barcode.BarcodeDetector()
            ok, decoded_info, _, points = bd.detectAndDecode(frame)
            if ok and decoded_info:
                for i, code in enumerate(decoded_info):
                    if code and code.strip():
                        pts = []
                        if points is not None and i < len(points):
                            pts = [(int(p[0]), int(p[1])) for p in points[i]]
                        results.append((code.strip(), pts))
    except Exception:
        pass

    return results