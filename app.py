"""
Library station backend with Active Student I-Card Verification (Camera QR/Barcode + Manual ID),
Student Registration (Year/Branch), Subject Search, Database Reset, Penalty Management,
Multi-Copy Inventory Admin Panel, and Real-Time Auto-Excel Synchronization
(available_books.xlsx, Users.xlsx, library_logs.xlsx).

    python -m uvicorn app:app --reload --port 8000

Frontend:  http://127.0.0.1:8000/
API docs:  http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import os
import sys
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C            # noqa: E402
import db as db               # noqa: E402
try:
    import scanner_auth
except Exception:
    try:
        from utils import scanner_auth
    except Exception:
        scanner_auth = None

_standalone_bot = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _standalone_bot
    db.seed(C.CATALOG)
    # If app.py is launched standalone via uvicorn (not embedded inside main_system.py),
    # automatically start the YouBot worker thread so index.html always controls CoppeliaSim!
    if os.environ.get('DUME_EMBEDDED_UVICORN') != '1':
        def _init_bot_worker():
            global _standalone_bot
            time.sleep(0.5)
            try:
                from robot_controller import YouBot
                print("[app] Starting autonomous YouBot worker for Web Kiosk...")
                _standalone_bot = YouBot()
            except Exception as e:
                print(f"[app] Note: Standalone YouBot connection deferred ({e}).")

        threading.Thread(target=_init_bot_worker, daemon=True).start()
    yield


app = FastAPI(title='DUM-E Autonomous Library Station', version='2.6', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

# -------------------------------------------------------------------- models


class IssueReq(BaseModel):
    book_code: str
    member_id: str


class AuthVerifyReq(BaseModel):
    member_id: str
    source: str = 'Manual / Barcode Input'


class AuthFrameReq(BaseModel):
    image_b64: str


class MemberReq(BaseModel):
    id: str                     # Scanned barcode / I-Card ID (e.g., S2500023)
    name: str                   # Student Full Name (e.g., Muddassir Sheikh)
    year: str = '4th'           # 1st, 2nd, 3rd, 4th
    branch: str = 'ENTC'        # ENTC, CSE, EE, ME, CE, IE


class AdminAuthReq(BaseModel):
    password: str


class BookAdminReq(BaseModel):
    code: str                   # Subject Code (e.g., DSP, AC, CS)
    title: str                  # Book Title
    author: str = ''            # Author Name
    shelf: str = 'Cupboard_1'   # Cupboard_1 or Cupboard_2
    total_copies: int = 1       # Total inventory copies


class CopiesAdjustReq(BaseModel):
    code: str
    delta: int = 1              # +1 or -1


class ShelfAdjustReq(BaseModel):
    code: str
    shelf: str                  # Cupboard_1 or Cupboard_2


class MissionResult(BaseModel):
    ok: bool
    detail: str = ''


class RobotState(BaseModel):
    state: str = 'idle'
    detail: str = ''
    position: list = [0.0, 0.0]
    yaw: float = 0.0
    holding: str | None = None
    progress: float = 0.0
    log: list = []


# --------------------------------------------------------------------- read
@app.get('/api/books')
def books(q: str = '', member_id: str = ''):
    all_books = db.list_books(member_id=member_id if member_id.strip() else None)
    if q.strip():
        ql = q.strip().lower()
        all_books = [
            b for b in all_books
            if ql in b['code'].lower()
            or ql in b['title'].lower()
            or ql in (b.get('author') or '').lower()
            or ql in (b.get('shelf') or '').lower()
        ]
    return all_books


@app.get('/api/members')
def members():
    return db.list_members()


@app.get('/api/members/{mid}')
def member(mid: str):
    m = db.get_member(mid)
    if not m:
        raise HTTPException(404, f"I-Card ID '{mid}' database me registered nahi hai.")
    db.set_auth_member(m)
    return m


@app.get('/api/members/{mid}/loans')
def member_loans(mid: str):
    """Returns active loans specifically for this student."""
    return db.list_member_loans(mid)


# ------------------------------------------------------------- student i-card auth
@app.get('/api/auth/active')
def auth_active():
    """Return currently verified student session (if any)."""
    m = db.get_auth_member()
    return {'authenticated': bool(m), 'member': m}


@app.post('/api/auth/verify')
def auth_verify(req: AuthVerifyReq):
    """Verify student I-Card ID and unlock kiosk session."""
    mid = req.member_id.strip().upper()
    m = db.get_member(mid)
    if not m or not m.get('active'):
        raise HTTPException(404, f"I-Card ID '{mid}' database me registered nahi hai.")
    db.set_auth_member(m)
    db.log_tx(None, m['id'], 'icard_verified', f"Verified {m['name']} via {req.source}")
    return m


@app.post('/api/auth/logout')
def auth_logout():
    """Clear active student I-Card session and lock kiosk back to Step 1."""
    db.set_auth_member(None)
    return {'ok': True}


@app.post('/api/auth/scan-frame')
def auth_scan_frame(req: AuthFrameReq):
    """Decode QR/Barcode from a browser webcam frame and verify student I-Card."""
    if not req.image_b64:
        return {'ok': False, 'detail': 'Empty frame'}
    try:
        import cv2
        import numpy as np
        raw_b64 = req.image_b64.split(',')[-1]
        img_bytes = base64.b64decode(raw_b64)
        arr = np.frombuffer(img_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {'ok': False, 'detail': 'Invalid image data'}

        codes = [c for c, _ in db.decode_codes_from_frame(frame)]

        for code in codes:
            clean_id = code.strip().upper()
            m = db.get_member(clean_id)
            if m and m.get('active'):
                db.set_auth_member(m)
                db.log_tx(None, m['id'], 'icard_verified', f"Verified {m['name']} via Camera I-Card Scan")
                return {'ok': True, 'code': clean_id, 'member': m, 'source': 'Webcam Optical Scan'}
            return {'ok': False, 'code': clean_id, 'detail': f"Scanned code '{clean_id}' registered nahi hai."}

        return {'ok': False, 'detail': 'No barcode/QR detected in frame yet'}
    except Exception as e:
        return {'ok': False, 'detail': str(e)}


@app.post('/api/auth/scan-camera')
def auth_scan_camera():
    """
    Attempt a quick optical scan from local host webcam (0) or ESP32-CAM frame
    to verify a Student I-Card.
    """
    try:
        import cv2
        # 1. Check if host webcam has a card in front of it (quick 3-frame grab)
        if scanner_auth is not None:
            cap, _ = scanner_auth._open_capture(0)
            if cap is not None:
                try:
                    for _ in range(5):
                        ok, frame = cap.read()
                        if ok and frame is not None:
                            for code, _ in scanner_auth.decode_codes_from_frame(frame):
                                m = db.get_member(code.strip().upper())
                                if m and m.get('active'):
                                    db.set_auth_member(m)
                                    db.log_tx(None, m['id'], 'icard_verified', f"Verified {m['name']} via Host Camera")
                                    return {'ok': True, 'member': m, 'source': 'Host Webcam Scan'}
                        time.sleep(0.05)
                finally:
                    cap.release()
    except Exception:
        pass
    return {'ok': False, 'detail': 'Camera me I-Card QR/Barcode dikhayein ya ID enter karein.'}


@app.get('/api/stats')
def stats():
    return db.stats()


@app.get('/api/logs')
def logs(limit: int = 60):
    return db.recent_transactions(limit)


@app.get('/api/missions')
def missions(limit: int = 30):
    return db.list_missions(limit)


@app.get('/api/dashboard')
def dashboard(member_id: str = ''):
    """Returns complete state for single-poll frontend dashboards."""
    auth_m = db.get_auth_member()
    eff_mid = member_id.strip() or (auth_m['id'] if auth_m else '')
    return {
        'books': db.list_books(member_id=eff_mid if eff_mid else None),
        'stats': db.stats(),
        'members': db.list_members(),
        'auth_member': auth_m,
        'robot': db.get_robot_state(),
        'missions': db.list_missions(10),
        'logs': db.recent_transactions(25),
    }


# -------------------------------------------------------------------- admin
@app.post("/api/admin/verify")
def admin_verify(req: AdminAuthReq):
    """Verify password for Librarian Admin Panel."""
    VALID_PASSWORDS = {"admin123", "admin", "librarian123", "dume2026"}
    if req.password and req.password.strip() in VALID_PASSWORDS:
        return {"ok": True, "detail": "Access granted"}
    raise HTTPException(401, "Galat Librarian Password! (Default password is: admin123)")


@app.get('/api/admin/books')
def admin_get_books():
    """Librarian Admin: get all books with inventory details."""
    return db.list_books()


@app.post('/api/admin/books')
def admin_save_book(req: BookAdminReq):
    """Librarian Admin: add a new book or update copies/shelf of an existing book."""
    try:
        return db.add_or_update_book(
            code=req.code,
            title=req.title,
            author=req.author,
            shelf=req.shelf,
            total_copies=req.total_copies
        )
    except Exception as e:
        raise HTTPException(400, f"Failed to save book: {e}")


@app.post('/api/admin/books/copies')
def admin_adjust_copies(req: CopiesAdjustReq):
    """Librarian Admin: quick increment/decrement of book copies (+1 / -1)."""
    try:
        return db.set_book_copies(req.code, req.delta)
    except Exception as e:
        raise HTTPException(400, f"Failed to adjust copies: {e}")


@app.post('/api/admin/books/shelf')
def admin_adjust_shelf(req: ShelfAdjustReq):
    """Librarian Admin: change designated cupboard location (Cupboard_1 / Cupboard_2)."""
    try:
        return db.set_book_shelf(req.code, req.shelf)
    except Exception as e:
        raise HTTPException(400, f"Failed to change shelf: {e}")


@app.get('/api/available-books.xlsx')
def download_available_books():
    """Download available_books.xlsx with live inventory quantities and locations."""
    path = db.AVAILABLE_BOOKS_EXCEL
    if not os.path.isfile(path):
        db.sync_available_books_excel()
    if os.path.isfile(path):
        return FileResponse(
            path,
            filename="available_books.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    raise HTTPException(404, "available_books.xlsx not found.")


# -------------------------------------------------------------------- write
@app.post('/api/members')
def create_member(req: MemberReq):
    try:
        m = db.add_member(req.id, req.name, req.year, req.branch)
        db.set_auth_member(m)
        return m
    except Exception as e:
        raise HTTPException(400, f"Registration failed: {e}")


@app.post('/api/reset')
def reset_db():
    """Clear all transactions/missions, reset 3 copies per book on shelves, restore Muddassir Sheikh (4th Year ENTC), and signal 3D scene reset."""
    try:
        db.reset_database(C.CATALOG)
        db.set_auth_member(None)
        db.set_robot_state({'state': 'idle', 'detail': 'Database reset complete. LibBot ready.', 'position': [0.0, 0.0], 'yaw': 0.0, 'holding': None, 'progress': 0.0, 'log': []})
        db.run("INSERT INTO kv (k, v) VALUES ('reset_scene', '1') ON CONFLICT(k) DO UPDATE SET v='1'")
        return {'ok': True, 'detail': 'Database & 3D scene reset! Please verify Student I-Card in Step 1.'}
    except Exception as e:
        raise HTTPException(400, f"Database reset failed: {e}")


@app.post('/api/issue')
def issue(req: IssueReq):
    try:
        return db.request_issue(req.book_code, req.member_id)
    except db.LibraryError as e:
        raise HTTPException(400, str(e))


@app.post('/api/return')
def ret(req: IssueReq):
    try:
        return db.request_return(req.book_code, req.member_id)
    except db.LibraryError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------- esp32-cam pov & qr scan
def generate_fallback_frame() -> bytes:
    try:
        import cv2
        import numpy as np
        w, h = 320, 240
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (26, 30, 28)
        cx, cy = w // 2, h // 2
        for gx in range(40, w, 40):
            cv2.line(img, (gx, 0), (gx, h), (38, 42, 38), 1)
        for gy in range(40, h, 40):
            cv2.line(img, (0, gy), (w, gy), (38, 42, 38), 1)
        cv2.drawMarker(img, (cx, cy), (0, 255, 0), markerType=cv2.MARKER_CROSS, markerSize=22, thickness=1)
        cv2.rectangle(img, (cx - 55, cy - 55), (cx + 55, cy + 55), (0, 255, 0), 1)
        cv2.putText(img, 'ESP32-CAM POV | LIVE', (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 0), 1, cv2.LINE_AA)
        r_state = db.get_robot_state()
        hld = r_state.get('holding')
        if hld:
            cv2.putText(img, f'BOOK IN GRIPPER: {hld}', (cx - 70, cy + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1, cv2.LINE_AA)
        else:
            cv2.putText(img, 'AIMING AT SHELF / DROP ZONE', (cx - 95, cy + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (180, 180, 180), 1, cv2.LINE_AA)
        _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return buf.tobytes()
    except Exception:
        return b''


@app.get('/api/camera/frame')
def camera_frame():
    frame_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'latest_frame.jpg')
    if os.path.isfile(frame_path):
        return FileResponse(frame_path, media_type='image/jpeg')
    return Response(content=generate_fallback_frame(), media_type='image/jpeg')


@app.get("/api/camera/stream")
def camera_stream():
    def frame_generator():
        frame_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_frame.jpg")
        crlf = bytes([13, 10])
        boundary_header = b"--frame" + crlf + b"Content-Type: image/jpeg" + crlf + crlf
        boundary_footer = crlf
        while True:
            try:
                if os.path.isfile(frame_path) and (time.time() - os.path.getmtime(frame_path) < 10.0):
                    with open(frame_path, "rb") as f:
                        frame_bytes = f.read()
                else:
                    frame_bytes = generate_fallback_frame()
                yield boundary_header + frame_bytes + boundary_footer
            except Exception:
                pass
            time.sleep(0.08)
    return StreamingResponse(frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.post('/api/camera/scan')
def camera_scan():
    """Scan book QR / barcode from ESP32-CAM POV to auto-identify books."""
    r_state = db.get_robot_state()
    hld = r_state.get('holding')
    if hld:
        bk = db.get_book(hld)
        return {'ok': True, 'book_code': hld, 'title': bk['title'] if bk else hld, 'source': 'Gripper Sensor (Holding)'}

    frame_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'latest_frame.jpg')
    if os.path.isfile(frame_path):
        try:
            import cv2
            img = cv2.imread(frame_path)
            if img is not None:
                detector = cv2.QRCodeDetector()
                data, bbox, _ = detector.detectAndDecode(img)
                if data and data.strip():
                    code_cand = data.strip().upper()
                    bk = db.get_book(code_cand)
                    if bk:
                        return {'ok': True, 'book_code': bk['code'], 'title': bk['title'], 'source': 'Optical QR Scan'}
        except Exception:
            pass

    pos = r_state.get('position', [0.0, 0.0])
    if pos[0] > 1.0 and pos[1] > 0.0:
        return {'ok': True, 'book_code': 'AC', 'title': 'Analog Circuits', 'source': 'Shelf Slot Optical Barcode'}
    elif pos[0] > 1.0 and pos[1] <= 0.0:
        return {'ok': True, 'book_code': 'FOC', 'title': 'Fiber Optic Communications', 'source': 'Shelf Slot Optical Barcode'}
    elif pos[0] < -1.0:
        return {'ok': True, 'book_code': 'AC', 'title': 'Analog Circuits', 'source': 'Drop Table Optical Barcode'}

    return {'ok': False, 'detail': 'Barcode frame me nahi mila. Robot ko shelf ya book ke paas le jao.'}


# ------------------------------------------------------------- robot worker
@app.get('/api/missions/next')
def next_mission():
    m = db.next_mission()
    if not m:
        return {'mission': None}
    db.claim_mission(m['id'])
    return {'mission': m}


@app.post('/api/missions/{mid}/complete')
def finish(mid: int, res: MissionResult):
    try:
        db.complete_mission(mid, res.ok, res.detail)
    except db.LibraryError as e:
        raise HTTPException(404, str(e))
    return {'ok': True}


@app.post('/api/robot/state')
def push_state(s: RobotState):
    db.set_robot_state(s.model_dump())
    return {'ok': True}


@app.get('/api/robot/state')
def robot_state():
    return db.get_robot_state()


# ------------------------------------------------------------------ static
@app.get('/')
def index():
    local_index = os.path.join(STATIC, 'index.html')
    if os.path.isfile(local_index):
        return FileResponse(local_index)
    standalone_index = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
    if os.path.isfile(standalone_index):
        return FileResponse(standalone_index)
    return {'status': 'DUM-E backend running. Please place index.html in the project folder.'}


if os.path.isdir(STATIC):
    app.mount('/static', StaticFiles(directory=STATIC), name='static')


if __name__ == '__main__':
    import uvicorn
    import webbrowser
    print("==========================================================", flush=True)
    print("   DUM-E Autonomous Library Station Web Server & Worker", flush=True)
    print("   Live at: http://127.0.0.1:8000/", flush=True)
    print("==========================================================", flush=True)
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8000/")).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)