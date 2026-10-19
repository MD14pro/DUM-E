"""
scanner_auth.py
===============
Student I-Card Barcode / QR Scanner (ESP32-CAM, Local Webcam, or Frame Decode).
Supports pyzbar + OpenCV QRCodeDetector & BarcodeDetector fallback so it runs
out-of-the-box on any Windows/Linux PC even without external zbar DLLs.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Optional

import cv2
import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C
try:
    import db as db
except Exception:
    db = None

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
except Exception:
    pyzbar_decode = None


def decode_codes_from_frame(frame: np.ndarray) -> list[tuple[str, list[tuple[int, int]]]]:
    """
    Decode QR codes or 1D barcodes from a BGR image using pyzbar (if available)
    plus OpenCV QRCodeDetector and BarcodeDetector fallbacks.
    """
    results: list[tuple[str, list[tuple[int, int]]]] = []
    if frame is None or frame.size == 0:
        return results

    # 1. Try pyzbar first (handles Code128, Code39, EAN, QR, etc.)
    if pyzbar_decode is not None:
        try:
            for obj in pyzbar_decode(frame):
                code = obj.data.decode('utf-8', 'ignore').strip()
                if code:
                    pts = [(p.x, p.y) for p in getattr(obj, 'polygon', [])] or []
                    results.append((code, pts))
        except Exception:
            pass

    if results:
        return results

    # 2. Fallback: OpenCV QRCodeDetector
    try:
        qr = cv2.QRCodeDetector()
        data, bbox, _ = qr.detectAndDecode(frame)
        if data and data.strip():
            pts = []
            if bbox is not None and len(bbox) > 0:
                pts = [(int(p[0]), int(p[1])) for p in bbox[0]]
            results.append((data.strip(), pts))
    except Exception:
        pass

    # 3. Fallback: OpenCV BarcodeDetector (if available in opencv build)
    if not results and hasattr(cv2, 'barcode') and hasattr(cv2.barcode, 'BarcodeDetector'):
        try:
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


def _open_capture(stream_url):
    if stream_url is not None and stream_url != 0:
        try:
            cap = cv2.VideoCapture(stream_url)
            if cap.isOpened():
                return cap, stream_url
            cap.release()
        except Exception:
            pass
        print('[auth] ESP32-CAM stream offline — switching to local webcam (0)...')

    try:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            return cap, 0
        cap.release()
    except Exception:
        pass
    return None, None


def lookup_member(member_id: str) -> Optional[dict]:
    """Verify student I-Card ID against local SQLite DB or FastAPI backend."""
    clean_id = member_id.strip().upper()
    if not clean_id:
        return None

    # 1. Direct DB check if running in unified process
    if db is not None:
        try:
            m = db.get_member(clean_id)
            if m and m.get('active'):
                db.set_auth_member(m)
                db.log_tx(None, m['id'], 'icard_verified', f"Verified {m['name']} ({m.get('year', '4th')} Year {m.get('branch', 'ENTC')})")
                return m
        except Exception:
            pass

    # 2. HTTP Backend check
    try:
        r = requests.get(f'{C.BACKEND_URL}/api/members/{clean_id}', timeout=2.5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        print('[auth] Backend offline — checking fallback whitelist.')
        if clean_id == C.FALLBACK_MEMBER_ID:
            return {'id': clean_id, 'name': 'Muddassir Sheikh', 'year': '4th', 'branch': 'ENTC', 'active': 1, 'open_loans': 0, 'max_books': 3, 'penalty': 0.0}
        return None


def authenticate_user(stream_url=None, timeout: float = 20.0,
                      show_window: bool = True) -> Optional[dict]:
    """
    Scan Student I-Card via ESP32-CAM or Local Webcam, or detect if student
    already verified on the Web Kiosk (http://127.0.0.1:8000/).
    Returns the verified member dict on success, or None on timeout/cancel.
    """
    if db is not None:
        existing = db.get_auth_member()
        if existing and existing.get('active'):
            return existing

    stream_url = C.ESP32_CAM_URL if stream_url is None else stream_url
    cap, used = _open_capture(stream_url)
    if cap is None:
        print('[auth] Local webcam/ESP32-CAM not detected.')
        return None

    print(f'[auth] Camera active on ({used}). Show Student I-Card QR/Barcode (or verify on Web Kiosk / press q to skip camera).')

    t0 = time.time()
    last_code, last_t = None, 0.0
    member = None
    banner, banner_until = '', 0.0

    try:
        while time.time() - t0 < timeout:
            if db is not None:
                web_m = db.get_auth_member()
                if web_m and web_m.get('active'):
                    print(f"[auth] Verified via Web Kiosk: {web_m['name']} ({web_m['id']})")
                    member = web_m
                    break

            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue

            for code, pts in decode_codes_from_frame(frame):
                if code == last_code and time.time() - last_t < C.SCAN_COOLDOWN:
                    continue
                last_code, last_t = code, time.time()

                m = lookup_member(code)
                if m and m.get('active'):
                    print(f"[auth] I-Card Verified: {m['name']} ({m['id']})")
                    member = m
                    banner, banner_until = f"Verified: {m['name']} ({m['id']})", time.time() + 1.0
                else:
                    print(f"[auth] Unregistered I-Card: {code}")
                    banner, banner_until = f"Unregistered ID: {code}", time.time() + 2.0

                if len(pts) >= 4:
                    cv2.polylines(frame, [np.array(pts, dtype=np.int32)], True,
                                  (60, 200, 90) if member else (60, 60, 230), 2)

            if show_window:
                msg = banner if time.time() < banner_until else 'Scan Student I-Card (q = use Web Kiosk / Manual ID)'
                colour = (60, 200, 90) if member else (245, 245, 245)
                cv2.putText(frame, msg, (16, 34), cv2.FONT_HERSHEY_SIMPLEX,
                            0.62, colour, 2, cv2.LINE_AA)
                left = int(max(0, timeout - (time.time() - t0)))
                cv2.putText(frame, f'Timeout: {left}s', (16, frame.shape[0] - 16),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (180, 220, 180), 1, cv2.LINE_AA)
                cv2.imshow('DUM-E Student I-Card Verification', frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q') or key == 27:
                    print('[auth] Camera scan closed — switching to Manual / Web Kiosk verification.')
                    break

            if member and time.time() > banner_until:
                break
    finally:
        cap.release()
        if show_window:
            try:
                cv2.destroyAllWindows()
                for _ in range(4):
                    cv2.waitKey(1)
            except Exception:
                pass

    return member


if __name__ == '__main__':
    print(authenticate_user(0))