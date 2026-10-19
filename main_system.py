"""
main_system.py
==============
Autonomous Library youBot Station Unified Orchestrator & Web Kiosk Worker (v2.6).
Starts the FastAPI Web Dashboard (http://127.0.0.1:8000/) in a background thread,
enforces Student I-Card Verification (Auth), connects DUM-E (`YouBot`) to
CoppeliaSim, and processes both Web Kiosk (`index.html`) dispatches and CLI
commands (`task >`).

Usage:
------
    python main_system.py
    python main_system.py --no-auth
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C
import db as db
from robot_controller import YouBot
try:
    import scanner_auth
except Exception:
    try:
        from utils import scanner_auth
    except Exception:
        scanner_auth = None


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def start_background_web_server(host: str = '127.0.0.1', port: int = 8000, open_browser: bool = True):
    """Ensure the FastAPI Web Kiosk (index.html) is serving on http://127.0.0.1:8000/."""
    os.environ['DUME_EMBEDDED_UVICORN'] = '1'
    url = f"http://{host}:{port}/"

    if _is_port_open(host, port):
        print(f"[system] Web Dashboard already running at {url}")
        if open_browser:
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                pass
        return

    def _run_uvicorn():
        try:
            import uvicorn
            import app as fastapi_app
            uvicorn.run(fastapi_app.app, host=host, port=port, log_level="warning")
        except Exception as e:
            print(f"[web warn] Could not start embedded FastAPI server: {e}")

    t = threading.Thread(target=_run_uvicorn, daemon=True)
    t.start()

    for _ in range(30):
        if _is_port_open(host, port):
            break
        time.sleep(0.1)

    print(f"[system] Web Dashboard live at {url}")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass


def print_banner(bot: YouBot, require_auth: bool = True):
    auth_m = db.get_auth_member()
    print("\n" + "=" * 62)
    if auth_m:
        print(f"  Verified Student : {auth_m['name']} ({auth_m['id']} · {auth_m.get('year','4th')} Yr {auth_m.get('branch','ENTC')})")
    elif require_auth:
        print("  Auth Status      : LOCKED — Verify I-Card on Web Kiosk (Step 1)")
        print("                     or type I-Card ID (e.g. 'S2500023') / 'scan'")
    else:
        print("  Operator Mode    : Auth Bypassed (--no-auth)")
    print("  Available in Cupboard_1 & Cupboard_2:")
    books = db.list_books()
    for b in books:
        status_tag = f"[{b['status']} · {b.get('available_copies', 3)}/{b.get('total_copies', 3)}]"
        print(f"    {b['code']:<4} {b['title']:<36} {status_tag}")
    print("  Commands: 'S2500023' (verify ID) | 'scan' (camera auth) | 'bring AC' | 'return AC' | 'logout' | 'exit'")
    print("  (Web Dashboard active at http://127.0.0.1:8000/)")
    print("=" * 62 + "\n")


def parse_and_execute(cmd_str: str, bot: YouBot, require_auth: bool = True) -> bool:
    cmd = cmd_str.strip()
    if not cmd:
        return True
    if cmd.lower() in ('exit', 'quit', 'q'):
        return False

    if cmd.lower() == 'logout':
        db.set_auth_member(None)
        print("[auth] Student logged out. Please verify I-Card before next transaction.")
        return True

    if cmd.lower() in ('scan', 'auth scan', 'camera'):
        if scanner_auth is not None:
            m = scanner_auth.authenticate_user(timeout=20.0, show_window=True)
            if m:
                print(f"[auth] Verified: {m['name']} ({m['id']}) — Kiosk & CLI unlocked!")
            else:
                print("[auth] Camera scan cancelled or no card detected. Type I-Card ID (e.g. S2500023) to verify.")
        else:
            print("[auth] Scanner module unavailable — type I-Card ID directly (e.g. S2500023).")
        return True

    # Check if user typed a Student I-Card ID (e.g., S2500023 or 'auth S2500023')
    cand_id = cmd.upper()
    if cand_id.startswith('AUTH '):
        cand_id = cand_id[5:].strip()
    m_direct = db.get_member(cand_id)
    if m_direct and m_direct.get('active'):
        db.set_auth_member(m_direct)
        db.log_tx(None, m_direct['id'], 'icard_verified', f"Verified {m_direct['name']} via Terminal CLI")
        print(f"[auth] ✓ I-Card Verified: {m_direct['name']} ({m_direct['id']} · {m_direct.get('year','4th')} Year {m_direct.get('branch','ENTC')})")
        print("       Web Kiosk (http://127.0.0.1:8000/) and CLI are now unlocked!")
        return True

    # Determine active authenticated student for book issue/return
    auth_m = db.get_auth_member()
    if require_auth and not auth_m:
        print("[auth] 🔒 Pehle Student I-Card verify karein! Web Kiosk (Step 1) me verify karein ya yahan ID type karein (e.g. 'S2500023' ya 'scan').")
        return True

    active_mid = auth_m['id'] if auth_m else C.FALLBACK_MEMBER_ID

    code_match = None
    for code in C.CATALOG.keys():
        if re.search(r'\b' + re.escape(code) + r'\b', cmd, re.I):
            code_match = code
            break
        if C.CATALOG[code]['title'].lower() in cmd.lower():
            code_match = code
            break

    is_return = any(w in cmd.lower() for w in ('return', 'wapas', 'shelve', 'put back', 'drop back'))

    if is_return:
        if not code_match:
            print("[planner] Kripya book code specify karein (jaise: 'return AC').")
            return True
        print(f"[planner] 'return {code_match}' for {active_mid}")
        print("[planner] rule-based plan banaya (4-step return).")
        print("\n--- execution start ---")
        ok = bot.return_book(code_match)
        if ok:
            bk = db.get_book(code_match) or {}
            tot = int(bk.get('total_copies') or 3)
            cur_avail = int(bk.get('available_copies') if bk.get('available_copies') is not None else 2)
            new_avail = min(tot, cur_avail + 1)
            db.run(
                "UPDATE books SET status='available', available_copies=?, shelf=?, issued_to=NULL WHERE code=?",
                (new_avail, db.home_shelf_for_code(code_match), code_match)
            )
            db.run(
                "UPDATE loans SET status='returned' WHERE id IN ("
                "SELECT id FROM loans WHERE UPPER(book_code)=UPPER(?) AND status='active' ORDER BY id DESC LIMIT 1)",
                (code_match,)
            )
            db.log_tx(code_match, active_mid, 'returned', 'robot ne wapas cupboard me rakh di')
            db.sync_to_excel()
            db.sync_users_excel()
            db.sync_available_books_excel()
            print("--- mission complete ---\n")
        else:
            print("--- mission failed ---\n")
        return True

    if code_match:
        print(f"[planner] 'bring {code_match}' for {active_mid}")
        print("[planner] rule-based plan banaya (4-step fetch).")
        print("\n--- execution start ---")
        ok = bot.fetch_book(code_match)
        if ok:
            bk = db.get_book(code_match) or {}
            avail = int(bk.get('available_copies') if bk.get('available_copies') is not None else 3)
            new_avail = max(0, avail - 1)
            new_status = 'available' if new_avail > 0 else 'issued'
            now_ts = db.now()
            db.run(
                "UPDATE books SET status=?, available_copies=?, shelf='Drop_Table', issued_to=?, issued_at=?, times_used=times_used+1 WHERE code=?",
                (new_status, new_avail, active_mid, now_ts, code_match)
            )
            db.run(
                "INSERT INTO loans (book_code, member_id, issued_at, due_at, status) VALUES (?, ?, ?, ?, 'active')",
                (code_match, active_mid, now_ts, now_ts)
            )
            db.log_tx(code_match, active_mid, 'issued', 'robot ne drop table pe rakh di')
            db.sync_to_excel()
            db.sync_users_excel()
            db.sync_available_books_excel()
            print("--- mission complete ---\n")
        else:
            print("--- mission failed ---\n")
        return True

    print(f"[planner] Samjh nahi aaya: '{cmd}'. Example: 'S2500023' (verify ID), 'bring AC', ya 'return AC'")
    return True


def main():
    parser = argparse.ArgumentParser(description='DUM-E Autonomous Library Station Orchestrator.')
    parser.add_argument('--no-auth', action='store_true', help='Skip student I-Card verification requirement')
    parser.add_argument('--camera-auth', action='store_true', help='Open OpenCV camera window on startup for I-Card scan')
    parser.add_argument('--no-browser', action='store_true', help='Do not auto-open web browser')
    parser.add_argument('--host', default='localhost', help='CoppeliaSim ZMQ host')
    parser.add_argument('--port', type=int, default=23000, help='CoppeliaSim ZMQ port')
    parser.add_argument('--web-port', type=int, default=8000, help='FastAPI Web Kiosk port')
    args = parser.parse_args()

    print("==========================================================", flush=True)
    print("   Autonomous Library youBot Station  v2.6", flush=True)
    print("==========================================================", flush=True)

    db.seed(C.CATALOG)
    if args.no_auth:
        m_def = db.get_member(C.FALLBACK_MEMBER_ID)
        db.set_auth_member(m_def)
        print("[system] Auth bypassed (--no-auth).", flush=True)
    else:
        # Require fresh Student I-Card Verification on startup
        db.set_auth_member(None)
        print("[auth] Student I-Card Verification (Auth) is ACTIVE.", flush=True)

    # 1. Start embedded FastAPI Web Kiosk on http://127.0.0.1:8000/
    start_background_web_server(host='127.0.0.1', port=args.web_port, open_browser=not args.no_browser)

    # 2. Connect DUM-E robot controller to CoppeliaSim
    print("[bot] Connecting to CoppeliaSim on port", args.port, "...", flush=True)
    bot = YouBot(host=args.host, port=args.port)

    # 3. Optional startup OpenCV camera scan if --camera-auth is passed
    if not args.no_auth and args.camera_auth and scanner_auth is not None:
        scanner_auth.authenticate_user(timeout=20.0, show_window=True)

    print_banner(bot, require_auth=not args.no_auth)

    while True:
        try:
            cmd = input("task > ")
            cont = parse_and_execute(cmd, bot, require_auth=not args.no_auth)
            if not cont:
                break
        except EOFError:
            try:
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                break
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[error] {e}", flush=True)

    print("\n[system] LibBot offline.")
    return 0


if __name__ == '__main__':
    sys.exit(main())