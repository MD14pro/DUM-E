"""
Library station backend.

    python -m uvicorn backend.app:app --reload --port 8000

Frontend:  http://127.0.0.1:8000/
API docs:  http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as C            # noqa: E402
import db as db        # noqa: E402

from contextlib import asynccontextmanager    # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.seed(C.CATALOG)
    yield


app = FastAPI(title='Autonomous Library Station', version='2.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

# -------------------------------------------------------------------- write


class IssueReq(BaseModel):
    book_code: str
    member_id: str


class MemberReq(BaseModel):
    id: str
    name: str
    branch: str = ''


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
def books():
    return db.list_books()


@app.get('/api/members')
def members():
    return db.list_members()


@app.get('/api/members/{mid}')
def member(mid: str):
    m = db.get_member(mid)
    if not m:
        raise HTTPException(404, f'Member {mid} not found')
    m['open_loans'] = db.open_loans(mid)
    return m


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
def dashboard():
    """Frontend ek hi call me sab kuch le leta hai - polling sasti rehti hai."""
    return {
        'books': db.list_books(),
        'stats': db.stats(),
        'robot': db.get_robot_state(),
        'missions': db.list_missions(10),
        'logs': db.recent_transactions(25),
    }


# -------------------------------------------------------------------- write
@app.post('/api/members')
def create_member(req: MemberReq):
    return db.add_member(req.id, req.name, req.branch)


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
    return FileResponse(os.path.join(STATIC, 'index.html'))


app.mount('/static', StaticFiles(directory=STATIC), name='static')
