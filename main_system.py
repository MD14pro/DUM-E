"""
Autonomous library station - main entry point.

    python main_system.py                 # scan card, then CLI commands
    python main_system.py --mode worker   # automatically picks up missions from web UI
    python main_system.py --no-auth       # skip scanner for testing
    python main_system.py --camera 0      # force local webcam
"""

from __future__ import annotations

import argparse
import sys
import threading
import time

import requests

import config as C
from llm_agent import execute_plan, fetch_catalog, get_llm_plan
from robot_controller import YouBot


# --------------------------------------------------------------- telemetry
class BackendLink:
    """Sends robot status to backend (best effort, non-blocking)."""

    def __init__(self, url: str = C.BACKEND_URL):
        self.url = url
        self.session = requests.Session()
        self.online = self._ping()
        self._pending = None
        self._lock = threading.Lock()
        if self.online:
            threading.Thread(target=self._pump, daemon=True).start()
        else:
            print('[link] Backend offline - UI will not update, but bot will still operate.')

    def _ping(self) -> bool:
        try:
            self.session.get(f'{self.url}/api/stats', timeout=2).raise_for_status()
            return True
        except Exception:
            return False

    def push(self, tm):
        with self._lock:
            self._pending = {
                'state': tm.state, 'detail': tm.detail,
                'position': list(tm.position), 'yaw': tm.yaw,
                'holding': tm.holding, 'progress': tm.progress,
                'log': tm.log[-12:],
            }

    def _pump(self):
        while True:
            time.sleep(0.4)
            with self._lock:
                payload, self._pending = self._pending, None
            if payload:
                try:
                    self.session.post(f'{self.url}/api/robot/state',
                                      json=payload, timeout=2)
                except Exception:
                    pass

    def next_mission(self):
        if not self.online:
            return None
        try:
            return self.session.get(f'{self.url}/api/missions/next',
                                    timeout=4).json().get('mission')
        except Exception:
            return None

    def finish(self, mid: int, ok: bool, detail: str = ''):
        if not self.online:
            return
        try:
            self.session.post(f'{self.url}/api/missions/{mid}/complete',
                              json={'ok': ok, 'detail': detail}, timeout=4)
        except Exception:
            pass


# ------------------------------------------------------------------ modes
def run_mission(bot: YouBot, kind: str, code: str) -> tuple[bool, str]:
    try:
        if kind == 'fetch':
            if not bot.go_to_book(code):
                return False, 'Could not reach shelf'
            if not bot.pick_book(code):
                return False, 'Grasp failed'
            if not bot.go_to_table():
                return False, 'Could not reach table'
            if not bot.place_on_table():
                return False, 'Placement verification failed'
            return True, 'Delivered to drop table'
        if kind == 'return':
            if not bot.go_to_table():
                return False, 'Could not reach table'
            if not bot.shelve_book(code):
                return False, 'Shelving failed'
            return True, 'Returned to Cupboard_2'
        return False, f'Unknown mission kind {kind}'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'


def worker_loop(bot: YouBot, link: BackendLink):
    print('\n[worker] Waiting for Web UI missions... (Ctrl+C to stop)')
    idle_note = True
    while True:
        m = link.next_mission()
        if not m:
            if idle_note:
                bot.say('Standing by, waiting for mission.', state='idle')
                idle_note = False
            time.sleep(1.2)
            continue
        idle_note = True
        print(f"\n[worker] mission #{m['id']}: {m['kind']} {m['book_code']} "
              f"for {m['member_id']}")
        ok, detail = run_mission(bot, m['kind'], m['book_code'])
        link.finish(m['id'], ok, detail)
        bot.say(('Mission done: ' if ok else 'Mission fail: ') + detail,
                state='idle' if ok else 'error', progress=1.0 if ok else 0.0)
        bot.go_home()


def desk_loop(bot: YouBot, member: dict | None):
    cat = fetch_catalog()
    print('\n' + '=' * 58)
    print(f"  Welcome {member['name'] if member else 'operator'}")
    print('  Available in Cupboard_1:')
    for code, b in sorted(cat.items()):
        print(f"    {code:<4} {b['title']:<38} [{b.get('status','available')}]")
    print('  Example: "bring me network theory"  |  "return ADC"  |  exit')
    print('=' * 58)

    while True:
        try:
            cmd = input('\ntask > ').strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not cmd:
            continue
        if cmd.lower() in ('exit', 'quit', 'lock', 'q'):
            break
        plan = get_llm_plan(cmd, cat)
        if not plan:
            continue
        execute_plan(bot, plan)
        cat = fetch_catalog()


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['desk', 'worker'], default='desk')
    ap.add_argument('--no-auth', action='store_true')
    ap.add_argument('--calibrate', action='store_true',
                    help='Test forward/strafe/rotate to verify WHEEL_SIGNS')
    ap.add_argument('--camera', default=None,
                    help="0 for local webcam, or ESP32 stream URL")
    args = ap.parse_args()

    print('=' * 58)
    print('   Autonomous Library youBot Station  v2')
    print('=' * 58)

    member = None
    if not args.no_auth:
        from utils.scanner_auth import authenticate_user
        cam = args.camera
        if cam is not None and str(cam).isdigit():
            cam = int(cam)
        member = authenticate_user(cam)
        if not member:
            print('[system] Locked. Unverified card.')
            return 1
    else:
        print('[system] Auth skipped (--no-auth).')

    link = BackendLink()
    bot = YouBot(on_telemetry=link.push)

    if args.calibrate:
        bot.calibrate_base()

    try:
        if args.mode == 'worker':
            worker_loop(bot, link)
        else:
            desk_loop(bot, member)
    except KeyboardInterrupt:
        pass
    finally:
        print('\n[system] Shutting down - moving robot to safe pose.')
        try:
            bot.stop_base()
            bot.go_home()
            bot.say('Station locked.', state='offline')
            link.push(bot.tm)
            time.sleep(0.8)
            bot.shutdown()
        except Exception:
            pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
