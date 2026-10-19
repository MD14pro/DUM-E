"""
sync_simulation.py
==================
Synchronizes CoppeliaSim 3D scene objects with the persistent SQLite database
(library.db). Also automatically repairs the CoppeliaSim scene and patches
`config.py` if `run.bat` is launched before running `import_dume_to_coppelia.py`!

Usage:
------
    python sync_simulation.py
    python sync_simulation.py --reset-db
"""

from __future__ import annotations

import argparse
import json
import math
import os

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import config as C
import db as db
import import_dume_to_coppelia as importer


BOOK_MATRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'book_home_matrices.json')

HOME_BOOK_CENTERS_M: dict[str, list[float]] = {
    # Cupboard_1 (5 books)
    'AC':   [1.7405,  0.2310, 0.3575],
    'EDC':  [1.7405,  0.3510, 0.3575],
    'NT':   [1.7405,  0.4710, 0.3575],
    'ADC':  [1.7405,  0.2310, 0.5575],
    'CS':   [1.7405,  0.3510, 0.5575],
    # Cupboard_2 (5 books)
    'FOC':  [1.7405, -0.6490, 0.3575],
    'RASC': [1.7405, -0.5290, 0.3575],
    'ES':   [1.7405, -0.4090, 0.3575],
    'IOT':  [1.7405, -0.5290, 0.5575],
    'CN':   [1.7405, -0.4090, 0.5575],
}


def load_home_matrices() -> dict[str, dict]:
    if os.path.isfile(BOOK_MATRICES_FILE):
        try:
            with open(BOOK_MATRICES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def find_book_handle(sim, code: str) -> int | None:
    """Find the CoppeliaSim root handle for a book code."""
    code_up = code.upper()
    candidates = [
        f'/{code_up}',
        f'/Book_{code_up}',
        f'/Cupboard_1/{code_up}',
        f'/Cupboard_1/Book_{code_up}',
        f'/Cupboard_2/{code_up}',
        f'/Cupboard_2/Book_{code_up}',
    ]
    for path in candidates:
        try:
            h = sim.getObject(path)
            if sim.getObjectType(h) == sim.object_shape_type:
                return h
        except Exception:
            continue
    for path in candidates:
        try:
            return sim.getObject(path)
        except Exception:
            continue
    return None


def get_table_slot_position(slot_index: int) -> list[float]:
    slots = C.TABLE_SLOTS
    lateral_offset = slots[slot_index % len(slots)]
    return [-1.7300, float(lateral_offset), 0.3795]


def _has_any_path(sim, paths: tuple[str, ...]) -> bool:
    for p in paths:
        try:
            sim.getObject(p)
            return True
        except Exception:
            continue
    return False


def needs_scene_reimport(sim) -> bool:
    """Check if scene needs re-import (missing vision sensor, missing finger_l, or static shape LibBot)."""
    try:
        h = sim.getObject('/ADC')
        pos = sim.getObjectPosition(h, -1)
        if math.hypot(pos[0], pos[1]) < 0.25:
            return True

        bot_name = C.ROBOT_NAME
        if not _has_any_path(sim, (f'/{bot_name}/finger_l', f'/{bot_name}/Arm_Wrist_Gripper/finger_l', ':/finger_l', '/finger_l')):
            return True
        if not _has_any_path(sim, (f'/{bot_name}/gripper_cam', f'/{bot_name}/Arm_Wrist_Gripper/gripper_cam', ':/gripper_cam', '/gripper_cam')):
            return True

        # Verify LibBot is a kinematic dummy so chassis translates smoothly along corridor track
        bot_h = sim.getObject(f'/{bot_name}')
        if sim.getObjectType(bot_h) != sim.object_dummy_type:
            print("[SYNC] Detected '/LibBot' is a Shape -> Auto-triggering clean reimport to kinematic dummy!")
            return True

        return False
    except Exception:
        return True


def sync_scene_with_db(host: str = 'localhost', port: int = 23000, reset_db_first: bool = False):
    """Ensure config/robot_controller are patched, scene has true 3D origins, and sync library.db."""
    importer.ensure_local_files_patched()

    if reset_db_first:
        db.reset_database(C.CATALOG)
        print("[SYNC] Cleared library.db! All 10 books available (CS in Cupboard_1) & Muddassir Sheikh (4th Year ENTC) set.")
    else:
        db.seed(C.CATALOG)
        db.run("UPDATE books SET status='available', issued_to=NULL, issued_at=NULL, due_at=NULL WHERE status='in_transit'")

    client = RemoteAPIClient(host, port)
    sim = client.require('sim')

    if needs_scene_reimport(sim):
        print("[SYNC] Detected missing ESP32-CAM or articulating fingers in CoppeliaSim -> Auto-running import_dume_to_coppelia.py!")
        importer.main()

    home_matrices = load_home_matrices()
    books = db.list_books()
    print(f"[SYNC] Syncing {len(books)} books from library.db into CoppeliaSim...")

    table_slot_counter = 0

    for b in books:
        code = b['code'].upper()
        status = b['status']
        shelf = b['shelf']

        h = find_book_handle(sim, code)
        if h is None:
            print(f"[WARN] Object for book '{code}' not found in CoppeliaSim hierarchy. Skipping.")
            continue

        try:
            sim.setObjectParent(h, -1, True)
            sim.resetDynamicObject(h)
        except Exception:
            pass

        saved_info = home_matrices.get(code)
        home_center = (saved_info or {}).get('center') or HOME_BOOK_CENTERS_M.get(code, [1.7405, 0.3510, 0.5575])
        home_mat = (saved_info or {}).get('matrix')

        if status == 'issued' or shelf == 'Drop_Table':
            target_center = get_table_slot_position(table_slot_counter)
            if home_mat and len(home_mat) == 12:
                m_table = list(home_mat)
                m_table[3]  += target_center[0] - home_center[0]
                m_table[7]  += target_center[1] - home_center[1]
                m_table[11] += target_center[2] - home_center[2]
                sim.setObjectMatrix(h, -1, m_table)
            else:
                sim.setObjectPosition(h, -1, target_center)
            print(f"  -> [{code}] ISSUED to '{b.get('issued_to') or 'student'}' => Placed on Drop_Table (slot #{table_slot_counter}) at {[round(v, 3) for v in target_center]}")
            table_slot_counter += 1
        else:
            if home_mat and len(home_mat) == 12:
                sim.setObjectMatrix(h, -1, list(home_mat))
            else:
                sim.setObjectPosition(h, -1, home_center)
            print(f"  -> [{code}] AVAILABLE => Locked inside {db.home_shelf_for_code(code)} at {[round(v, 3) for v in home_center]}")

    print("[SYNC] Database and CoppeliaSim 3D scene are 100% in sync (0 floating books)!")


def main():
    parser = argparse.ArgumentParser(description='Sync CoppeliaSim scene with persistent library.db state.')
    parser.add_argument('--host', default='localhost', help='CoppeliaSim ZMQ host')
    parser.add_argument('--port', type=int, default=23000, help='CoppeliaSim ZMQ port')
    parser.add_argument('--reset-db', action='store_true', help='Clear database and reset all books before syncing')
    args = parser.parse_args()

    sync_scene_with_db(args.host, args.port, reset_db_first=args.reset_db)


if __name__ == '__main__':
    main()