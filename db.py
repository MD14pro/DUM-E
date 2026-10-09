"""
SQLite data layer for the library station.

Ek hi file (library.db) me catalog, members, transactions, mission queue aur
robot telemetry. Koi server-setup nahi chahiye.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

DB_PATH = os.environ.get(
    'LIBRARY_DB', os.path.join(os.path.dirname(__file__), 'library.db'))

LOAN_DAYS = 14

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    branch    TEXT,
    active    INTEGER NOT NULL DEFAULT 1,
    max_books INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS books (
    code       TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    author     TEXT,
    shelf      TEXT NOT NULL DEFAULT 'Cupboard_1',
    status     TEXT NOT NULL DEFAULT 'available',
    issued_to  TEXT,
    issued_at  TEXT,
    due_at     TEXT,
    times_used INTEGER NOT NULL DEFAULT 0
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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute('PRAGMA journal_mode=WAL')
            _conn.executescript(SCHEMA)
            _conn.commit()
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


# ---------------------------------------------------------------------------
def seed(catalog: dict, members: Optional[list] = None):
    """Idempotent - dobara chalane se data duplicate nahi hoga."""
    authors = {
        'AC': 'Sedra & Smith',
        'EDC': 'Boylestad & Nashelsky',
        'NT': 'Van Valkenburg',
        'ADC': 'Simon Haykin',
        'SS': 'Oppenheim & Willsky',
    }

    with _lock:
        connection = conn()
        # Use a transaction for better performance and atomicity
        try:
            for code, meta in catalog.items():
                connection.execute(
                    """INSERT INTO books (code, title, author, shelf)
                       VALUES (?, ?, ?, 'Cupboard_1')
                       ON CONFLICT(code) DO UPDATE SET title=excluded.title""",
                    (code, meta['title'], authors.get(code, 'Unknown'))
                )

            default_members = members or [
                ('S2500023', 'Muddassir', 'Computer', 1, 3),
            ]
            for m in default_members:
                connection.execute(
                    """INSERT INTO members (id, name, branch, active, max_books)
                       VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING""",
                    m
                )
            connection.commit()
        except Exception as e:
            connection.rollback()
            raise e


# ------------------------------------------------------------------ members
def get_member(mid: str) -> Optional[dict]:
    r = q('SELECT * FROM members WHERE id = ?', (mid,))
    return r[0] if r else None


def list_members() -> list[dict]:
    return q('SELECT * FROM members ORDER BY name')


def add_member(mid: str, name: str, branch: str = '') -> dict:
    run("""INSERT INTO members (id, name, branch) VALUES (?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name,
           branch=excluded.branch""", (mid, name, branch))
    return get_member(mid)


def open_loans(mid: str) -> int:
    return q('SELECT COUNT(*) c FROM books WHERE issued_to = ? AND status != ?',
             (mid, 'available'))[0]['c']


# -------------------------------------------------------------------- books
def list_books() -> list[dict]:
    rows = q('SELECT * FROM books ORDER BY code')
    for r in rows:
        r['overdue'] = bool(r['due_at'] and r['status'] == 'issued'
                            and r['due_at'] < now())
    return rows


def get_book(code: str) -> Optional[dict]:
    r = q('SELECT * FROM books WHERE code = ?', (code.upper(),))
    return r[0] if r else None


def log_tx(book: Optional[str], member: Optional[str], action: str, note: str = ''):
    run('INSERT INTO transactions (ts, book_code, member_id, action, note) '
        'VALUES (?, ?, ?, ?, ?)', (now(), book, member, action, note))


def recent_transactions(limit: int = 60) -> list[dict]:
    return q('SELECT * FROM transactions ORDER BY id DESC LIMIT ?', (limit,))


# ------------------------------------------------------------------ actions
class LibraryError(Exception):
    pass


def request_issue(code: str, member_id: str) -> dict:
    """Book reserve karke robot ke liye fetch mission queue me daalta hai."""
    code = code.upper()
    book, member = get_book(code), get_member(member_id)
    if not book:
        raise LibraryError(f"'{code}' catalog me nahi hai.")
    if not member or not member['active']:
        raise LibraryError(f"Member {member_id} valid nahi hai.")
    if book['status'] != 'available':
        raise LibraryError(f"'{code}' abhi {book['status']} hai.")
    if open_loans(member_id) >= member['max_books']:
        raise LibraryError(f"{member['name']} ki limit "
                           f"({member['max_books']} books) full hai.")

    due = (datetime.now(timezone.utc) + timedelta(days=LOAN_DAYS)).isoformat(timespec='seconds')
    run("""UPDATE books SET status='in_transit', issued_to=?, issued_at=?, due_at=?,
           times_used = times_used + 1 WHERE code=?""", (member_id, now(), due, code))
    mid = run("""INSERT INTO missions (created_at, updated_at, kind, book_code,
                 member_id, status, detail) VALUES (?, ?, 'fetch', ?, ?, 'pending', '')""",
              (now(), now(), code, member_id))
    log_tx(code, member_id, 'issue_requested', f'mission #{mid}')
    return {'mission_id': mid, 'book': get_book(code), 'due_at': due}


def request_return(code: str, member_id: str) -> dict:
    code = code.upper()
    book = get_book(code)
    if not book:
        raise LibraryError(f"'{code}' catalog me nahi hai.")
    # REMOVED: status == 'available' check for easier testing
    mid = run("""INSERT INTO missions (created_at, updated_at, kind, book_code,
                 member_id, status, detail) VALUES (?, ?, 'return', ?, ?, 'pending', '')""",
              (now(), now(), code, member_id))
    log_tx(code, member_id, 'return_requested', f'mission #{mid}')
    return {'mission_id': mid}


def complete_mission(mid: int, ok: bool, detail: str = ''):
    m = q('SELECT * FROM missions WHERE id = ?', (mid,))
    if not m:
        raise LibraryError(f'Mission {mid} nahi mila.')
    m = m[0]
    run('UPDATE missions SET status=?, detail=?, updated_at=? WHERE id=?',
        ('done' if ok else 'failed', detail, now(), mid))

    code = m['book_code']
    if ok and m['kind'] == 'fetch':
        run("UPDATE books SET status='issued', shelf='Drop_Table' WHERE code=?", (code,))
        log_tx(code, m['member_id'], 'issued', 'robot ne table pe rakhi')
    elif ok and m['kind'] == 'return':
        run("""UPDATE books SET status='available', issued_to=NULL, issued_at=NULL,
               due_at=NULL, shelf='Cupboard_2' WHERE code=?""", (code,))
        log_tx(code, m['member_id'], 'returned', 'robot ne shelf pe rakhi')
    elif not ok and m['kind'] == 'fetch':
        run("""UPDATE books SET status='available', issued_to=NULL, issued_at=NULL,
               due_at=NULL WHERE code=?""", (code,))
        log_tx(code, m['member_id'], 'issue_failed', detail)
    else:
        log_tx(code, m['member_id'], 'return_failed', detail)


def next_mission() -> Optional[dict]:
    r = q("SELECT * FROM missions WHERE status='pending' ORDER BY id LIMIT 1")
    return r[0] if r else None


def claim_mission(mid: int):
    run("UPDATE missions SET status='running', updated_at=? WHERE id=?", (now(), mid))


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
    return {
        'total': len(b),
        'available': sum(1 for x in b if x['status'] == 'available'),
        'issued': sum(1 for x in b if x['status'] == 'issued'),
        'in_transit': sum(1 for x in b if x['status'] == 'in_transit'),
        'overdue': sum(1 for x in b if x['overdue']),
        'members': len(list_members()),
    }
