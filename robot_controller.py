"""
LibBot controller.

  * Arm: closed-form IK (kinematics.py), Cartesian straight-line moves, har
    trajectory execute hone se PEHLE poori validate hoti hai (aadhi chal kar
    "unreachable" nahi).
  * Base: original mecanum wheels khud drive karte hai (wheel velocity, accel limited).
    Har wheel joint ka axis sign sim se padha jaata hai, to clone ki orientation
    kuch bhi ho, drive sahi chalta hai.
  * Gripper: dynamic fingers. Grasp verify finger opening se hota hai
    (book 4 cm ki hai -> fingers ~20 mm pe ruki), distance guess se nahi.
  * Waypoint routing deewaron se bachne ke liye.
"""
from __future__ import annotations

import atexit
import math
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

import numpy as np

import config as C
from kinematics import (arm_fk, arm_ik, mecanum_wheel_speeds, route)

for _p in (r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\programming\zmqRemoteApi\clients\python",
           r"C:\Program Files\CoppeliaRobotics\CoppeliaSimPlayer\programming\zmqRemoteApi\clients\python",
           "/opt/CoppeliaSim/programming/zmqRemoteApi\clients\python"):
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)

RemoteAPIClient = None
_IMPORT_ERR = None
for _m in ('coppeliasim_zmqremoteapi_client', 'zmqRemoteApi'):
    try:
        RemoteAPIClient = __import__(_m, fromlist=['RemoteAPIClient']).RemoteAPIClient
        break
    except ImportError as _e:
        _IMPORT_ERR = _e
if RemoteAPIClient is None:
    def RemoteAPIClient(*a, **k):                      # type: ignore[misc]
        raise RuntimeError('CoppeliaSim ZMQ client nahi mila:\n'
                           '    pip install coppeliasim-zmqremoteapi-client\n'
                           f'({_IMPORT_ERR})')


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def min_jerk(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5


def to_mat(m12) -> np.ndarray:
    M = np.eye(4)
    M[:3, :4] = np.array(m12, float).reshape(3, 4)
    return M


@dataclass
class Telemetry:
    state: str = 'idle'
    detail: str = ''
    position: tuple = (0.0, 0.0)
    yaw: float = 0.0
    holding: Optional[str] = None
    progress: float = 0.0
    log: list = field(default_factory=list)


class LibBot:
    def __init__(self, on_telemetry: Optional[Callable[[Telemetry], None]] = None,
                 stepping: bool = True, verbose: bool = True):
        # --- saara state PEHLE (say() inhe use karta hai) ---------------------
        self.verbose = verbose
        self.on_telemetry = on_telemetry
        self.tm = Telemetry()
        self.is_grasped = False
        self.held_book: Optional[str] = None
        self.held_handle: Optional[int] = None
        self._slot = 0
        self._cmd = np.zeros(3)
        self.wheel_sign: dict = {}
        self.h: dict = {}
        self.arm: list = []
        self.fingers: list = []
        self.wheels: dict = {}
        self.body = self.ref = self.palm = self.tip = None
        self._closed = False
        self.dt = C.SIM_TIME_STEP

        self.say('Connecting to CoppeliaSim...')
        self.client = RemoteAPIClient()
        self.sim = self.client.require('sim')
        self._ensure_running()
        self.stepping = stepping
        try:
            self.sim.setStepping(stepping)
        except Exception:
            self.stepping = False
            self.say('WARNING: stepping mode nahi mila - force control ke liye zaroori hai.')

        self._grab_handles()
        atexit.register(self.shutdown)
        self.stop_base(ramp=False)
        self.arm_carry(duration=1.0)
        self.gripper(C.GRIP_OPEN, wait=0.3)
        self.say('LibBot ready.', state='idle')

    # ------------------------------------------------------------------ util
    def say(self, msg: str, state: Optional[str] = None, progress: Optional[float] = None):
        if self.verbose:
            print(f'[bot] {msg}')
        self.tm.detail = msg
        self.tm.log = (self.tm.log + [msg])[-40:]
        if state:
            self.tm.state = state
        if progress is not None:
            self.tm.progress = progress
        try:
            x, y, yaw = self.pose()
            self.tm.position = (round(x, 3), round(y, 3))
            self.tm.yaw = round(yaw, 3)
        except Exception:
            pass
        self.tm.holding = self.held_book
        if self.on_telemetry:
            try:
                self.on_telemetry(self.tm)
            except Exception:
                pass

    def _ensure_running(self):
        try:
            if self.sim.getSimulationState() == self.sim.simulation_stopped:
                self.say('Simulation stopped thi - start kar raha hoon.')
                self.sim.startSimulation()
                time.sleep(1.5)  # Increased sleep to ensure ZMQ API is ready
        except Exception:
            pass

    def _raw_step(self):
        if self.stepping:
            self.client.step()
        else:
            time.sleep(self.dt)

    def step(self, n: int = 1):
        for _ in range(n):
            self._raw_step()

    def sleep(self, seconds: float):
        self.step(max(1, int(seconds / self.dt)))

    def shutdown(self):
        """Stepping band karna zaroori hai warna CoppeliaSim atka rehta hai."""
        if self._closed:
            return
        self._closed = True
        try:
            self.sim.setStepping(False)
        except Exception:
            pass

    def _grab_handles(self):
        sim = self.sim
        try:
            self.body = sim.getObject('/' + C.ROBOT_NAME)
        except Exception:
            raise RuntimeError(f"'/{C.ROBOT_NAME}' scene me nahi hai. Pehle chalao: python build_scene.py")
        self.h = {sim.getObjectAlias(t): t for t in
                  [self.body] + list(sim.getObjectsInTree(self.body, sim.handle_all, 0))}
        need = (['ref', 'palm', 'tip', 'arm_yaw', 'arm_shoulder', 'arm_elbow', 'arm_wrist',
                 'finger_l', 'finger_r'] + [f'rollingJoint_{k}' for k in ('fl', 'fr', 'rl', 'rr')])
        missing = [n for n in need if n not in self.h]
        if missing:
            raise RuntimeError(f'Robot adhoora hai, missing: {missing}. python build_scene.py dobara chalao.')
        self.ref, self.palm, self.tip = self.h['ref'], self.h['palm'], self.h['tip']
        self.arm = [self.h[n] for n in ('arm_yaw', 'arm_shoulder', 'arm_elbow', 'arm_wrist')]
        self.fingers = [self.h['finger_l'], self.h['finger_r']]
        self.wheels = {k: self.h[f'rollingJoint_{k}'] for k in ('fl', 'fr', 'rl', 'rr')}
        self.cam = self.h.get('gripper_cam')
        # har wheel joint ka axis (local z) robot frame me: +y ho to +1, -y ho to -1
        for k, wh in self.wheels.items():
            ay = float(to_mat(sim.getObjectMatrix(wh, self.ref))[1, 2])
            if abs(ay) < 0.7:
                self.say(f'WARNING: wheel {k} ka axis y se align nahi ({ay:+.2f}) - drive galat ho sakta hai.')
            self.wheel_sign[k] = 1.0 if ay >= 0 else -1.0

    # ------------------------------------------------------------------ pose
    def _ref_matrix(self) -> np.ndarray:
        return to_mat(self.sim.getObjectMatrix(self.ref, -1))

    def pose(self):
        M = self._ref_matrix()
        return float(M[0, 3]), float(M[1, 3]), math.atan2(M[1, 0], M[0, 0])

    def world_to_base(self, p_world: Sequence[float]) -> np.ndarray:
        return (np.linalg.inv(self._ref_matrix()) @ np.array([*p_world, 1.0]))[:3]

    def find(self, name: str) -> int:
        # Extreme retry loop to handle "Operation cannot be accomplished in current state"
        start_time = time.time()
        while (time.time() - start_time) < 2.0:
            try:
                # 1. Exact match check
                for p in (name if name.startswith('/') else f'/{name}',
                              f'/{C.SOURCE_SHELF}/{name}', f'/{C.RETURN_SHELF}/{name}', f'::/{name}'):
                    try:
                        h = self.sim.getObject(p)
                        if h is not None and h != -1:
                            return h
                    except Exception:
                        continue

                # 2. Fuzzy match check
                all_objects = self.sim.getObjectsInTree(self.sim.handle_scene, self.sim.handle_all, 0)
                target = name.lower()
                for h in all_objects:
                    alias = self.sim.getObjectAlias(h).lower()
                    if target in alias:
                        return h

                # If we got here, the API call worked but the object is simply not there
                return -1
            except Exception as e:
                if "current state" in str(e).lower():
                    time.sleep(0.1)
                    continue
                break

        return -1

    # ------------------------------------------------------------------ base
    def _drive_step(self, vx: float, vy: float, wz: float):
        """Optimized step: Ramp acceleration and smooth ZMQ calls."""
        want = np.array([vx, vy, wz], float)
        # Smooth ramping: don't let the robot jump instantly to speed
        lim = np.array([C.LIN_ACCEL, C.LIN_ACCEL, C.ROT_ACCEL]) * self.dt
        self._cmd = self._cmd + np.clip(want - self._cmd, -lim, lim)

        sg = C.DRIVE_SIGNS
        w = mecanum_wheel_speeds(sg[0] * self._cmd[0], sg[1] * self._cmd[1], sg[2] * self._cmd[2])
        for k, v in w.items():
            self.sim.setJointTargetVelocity(self.wheels[k], float(self.wheel_sign[k] * v))

        self._raw_step()

    def stop_base(self, ramp: bool = True):
        if ramp:
            # Slow down gradually to avoid 'jerk' and CoppeliaSim physics glitch
            for _ in range(int(0.5 / self.dt)):
                self._drive_step(0, 0, 0)
                if np.linalg.norm(self._cmd) < 1e-3:
                    break
        self._cmd = np.zeros(3)
        for w in self.wheels.values():
            self.sim.setJointTargetVelocity(w, 0.0)
        self.step(20) # Final settle time

    def drive_to(self, x: float, y: float, yaw: Optional[float] = None,
                 pos_tol: float = C.POS_TOLERANCE, timeout: float = C.NAV_TIMEOUT) -> bool:
        t0 = time.time()
        steps = int(timeout / self.dt)
        for _ in range(steps):
            bx, by, byaw = self.pose()
            dx, dy = x - bx, y - by
            dist = math.hypot(dx, dy)
            yaw_err = wrap(yaw - byaw) if yaw is not None else 0.0
            if dist < pos_tol and (yaw is None or abs(yaw_err) < C.YAW_TOLERANCE):
                self.stop_base()
                return True
            ex = math.cos(byaw) * dx + math.sin(byaw) * dy
            ey = -math.sin(byaw) * dx + math.cos(byaw) * dy
            speed = min(C.MAX_LIN_SPEED, C.POS_GAIN * dist + 0.04)
            if dist > 0.35:
                head = wrap(math.atan2(dy, dx) - byaw)
                wz = float(np.clip(C.HEADING_GAIN * head, -C.MAX_ROT_SPEED, C.MAX_ROT_SPEED))
                vx = speed * max(0.0, math.cos(head)) ** 2
                vy = 0.0
            else:
                vx = float(np.clip(C.POS_GAIN * ex, -0.6 * speed, 0.6 * speed))
                vy = float(np.clip(C.POS_GAIN * ey, -C.MAX_STRAFE_SPEED, C.MAX_STRAFE_SPEED))
                wz = float(np.clip(2.0 * yaw_err, -C.MAX_ROT_SPEED, C.MAX_ROT_SPEED))
            self._drive_step(vx, vy, wz)
        self.stop_base()
        self.say(f'[nav] timeout: ({x:.2f},{y:.2f}) tak nahi pahuncha.', state='error')
        return False

    def navigate(self, x: float, y: float, yaw: Optional[float] = None) -> bool:
        bx, by, _ = self.pose()
        pts = route((bx, by), (x, y))
        for i, (wx, wy) in enumerate(pts):
            last = i == len(pts) - 1
            if not self.drive_to(wx, wy, yaw if last else None,
                                 pos_tol=C.POS_TOLERANCE if last else 0.10):
                return False
        return True

    # ------------------------------------------------------------------- arm
    def _arm_state(self) -> list:
        g = self.sim.getJointPosition
        return [g(self.arm[0])] + [-g(j) for j in self.arm[1:]]     # sim_q = -th

    def _arm_set(self, q: Sequence[float]):
        s = self.sim.setJointTargetPosition
        s(self.arm[0], float(q[0]))
        for j, th in zip(self.arm[1:], q[1:]):
            s(j, float(-th))

    def arm_tip_base(self) -> np.ndarray:
        return arm_fk(self._arm_state())

    def tip_world(self) -> np.ndarray:
        return np.array(self.sim.getObjectPosition(self.tip, -1))

    def arm_move_base(self, pb: Sequence[float], duration: float = 1.5, phi: float = 0.0) -> bool:
        """Grasp point ko base frame ke target tak seedhi line me le jao."""
        q0 = self._arm_state()
        p0 = arm_fk(q0)
        phi0 = q0[1] + q0[2] + q0[3]
        goal = arm_ik(pb, phi)
        if goal is None:
            self.say(f'[arm] target {np.round(pb, 3)} reach/limits ke bahar.', state='error')
            return False
        n = max(3, int(duration / self.dt))
        pb = np.array(pb, float)
        traj, straight = [], True
        for i in range(1, n + 1):
            s = min_jerk(i / n)
            q = arm_ik(p0 + (pb - p0) * s, phi0 + (phi - phi0) * s)
            if q is None:
                straight = False
                break
            traj.append(q)
        if not straight:                      # seedhi line nahi, joint-space fallback
            traj = [[a + (b - a) * min_jerk(i / n) for a, b in zip(q0, goal)]
                    for i in range(1, n + 1)]
        for q in traj:
            self._arm_set(q)
            self.step()
        self.sleep(0.3)
        return True

    def arm_move_world(self, p_world, duration: float = 1.5, phi: float = 0.0) -> bool:
        return self.arm_move_base(self.world_to_base(p_world), duration, phi)

    def arm_carry(self, duration: float = 1.4) -> bool:
        return self.arm_move_base(C.CARRY_TIP, duration, 0.0)

    # --------------------------------------------------------------- gripper
    def gripper(self, opening: float, force: Optional[float] = None, wait: float = 0.6):
        for j in self.fingers:
            self.sim.setJointTargetForce(j, float(force or C.GRIP_FORCE))
            self.sim.setJointTargetPosition(j, float(opening))
        self.sleep(wait)

    def finger_opening(self) -> float:
        return float(np.mean([self.sim.getJointPosition(j) for j in self.fingers]))

    # ------------------------------------------------------------- high level
    def _front(self, name: str) -> np.ndarray:
        f = C.FRONT[name]
        return np.array([f[0], f[1], 0.0])

    def _stand(self, target_xy, front: np.ndarray, standoff: float):
        sx = target_xy[0] + front[0] * standoff
        sy = target_xy[1] + front[1] * standoff
        return sx, sy, math.atan2(-front[1], -front[0])

    def go_to_book(self, code: str) -> bool:
        h = self.find(code)
        if h == -1:
            self.say(f"'{code}' shelf pe nahi mili.", state='error')
            return False
        p = self.sim.getObjectPosition(h, -1)
        x, y, yaw = self._stand(p, self._front(C.SOURCE_SHELF), C.APPROACH_STANDOFF)
        self.say(f"'{code}' ki taraf ({p[0]:.2f}, {p[1]:.2f}).", state='driving', progress=0.15)
        return self.navigate(x, y, yaw)

    def _table_center(self) -> np.ndarray:
        h = self.find(C.DROP_TABLE)
        if h == -1:
            h = self.find(C.DROP_SPOT)
        if h == -1:
            raise RuntimeError('Drop_Table scene me nahi hai. python build_scene.py chalao.')
        return np.array(self.sim.getObjectPosition(h, -1))

    def _slot_lateral(self, idx: Optional[int] = None) -> float:
        return C.TABLE_SLOTS[(self._slot if idx is None else idx) % len(C.TABLE_SLOTS)]

    def go_to_table(self) -> bool:
        pt = self._table_center()
        f = self._front('Drop_Table')
        side = np.array([-f[1], f[0], 0.0])
        base = pt + side * self._slot_lateral()
        x, y, yaw = self._stand(base, f, C.TABLE_STANDOFF)
        self.say('Drop table ki taraf.', state='driving', progress=0.6)
        return self.navigate(x, y, yaw)

    def go_to_return_shelf(self) -> bool:
        h = self.find(C.RETURN_SHELF)
        if h == -1:
            self.say('Cupboard_2 nahi mila.', state='error')
            return False
        p = self.sim.getObjectPosition(h, -1)
        x, y, yaw = self._stand(p, self._front(C.RETURN_SHELF), C.APPROACH_STANDOFF)
        self.say('Return shelf (Cupboard_2) ki taraf.', state='driving', progress=0.7)
        return self.navigate(x, y, yaw)

    def _weld(self, h: int):
        if C.GRASP_MODE == 'rigid':
            self.sim.setObjectParent(h, self.palm, True)
            self.sim.setObjectInt32Param(h, self.sim.shapeintparam_static, 1)
            self.sim.resetDynamicObject(h)

    def _unweld(self, h: int):
        if C.GRASP_MODE == 'rigid':
            self.sim.setObjectParent(h, -1, True)
            self.sim.setObjectInt32Param(h, self.sim.shapeintparam_static, 0)
            self.sim.resetDynamicObject(h)

    def _pick(self, code: str, handle: int, front: np.ndarray) -> bool:
        p = np.array(self.sim.getObjectPosition(handle, -1))
        grasp = p + front * C.GRASP_DEPTH + np.array([0, 0, C.GRASP_Z_OFFSET])
        pre = grasp + front * C.PRE_GRASP_OFFSET
        # pehle poori sequence ki reach check karo, phir chalo
        for name, pt in (('pre-grasp', pre), ('grasp', grasp),
                         ('lift', grasp + [0, 0, C.LIFT_HEIGHT]),
                         ('pull', pre + [0, 0, C.LIFT_HEIGHT])):
            if arm_ik(self.world_to_base(pt), 0.0) is None:
                self.say(f"'{code}': {name} point reach se bahar. Robot sahi jagah pe hai? "
                         f'check_setup.py chalao.', state='error')
                return False
        self.say(f"'{code}' ke liye arm aage badha raha hoon.", state='picking', progress=0.3)
        self.gripper(C.GRIP_OPEN, wait=0.4)
        if not self.arm_move_world(pre, 1.6):
            return False
        if not self.arm_move_world(grasp, 1.2):
            return False
        self.say('Gripper band (force controlled).', progress=0.42)
        self.gripper(0.0, wait=0.7)
        opening = self.finger_opening()
        self.say(f'Grip check: finger opening {opening*1000:.1f} mm.')
        if opening < C.GRIP_MIN_HOLD:
            self.say('Grip miss: kuch pakda nahi. Wapas aa raha hoon.', state='error')
            self.gripper(C.GRIP_OPEN, wait=0.3)
            self.arm_move_world(pre, 1.0)
            self.arm_carry()
            return False
        self._weld(handle)
        self.is_grasped, self.held_book, self.held_handle = True, code, handle
        self.say(f"'{code}' grasped.", progress=0.5)

        # FIX: Added settling time and higher lift to avoid "Wheelie-Stoppie" (friction)
        self.step(100) # Settle physics
        self.arm_move_world(grasp + [0, 0, C.LIFT_HEIGHT + 0.05], 0.8) # Lift slightly higher
        self.step(50)
        self.arm_move_world(pre + [0, 0, C.LIFT_HEIGHT + 0.05], 1.0)
        self.arm_carry()
        self.say('Carry pose set.', progress=0.55)
        return True

    def pick_book(self, code: str) -> bool:
        h = self.find(code)
        if h == -1:
            self.say(f"Book '{code}' missing.", state='error')
            return False
        return self._pick(code, h, self._front(C.SOURCE_SHELF))

    def _place(self, center: np.ndarray, front: np.ndarray, label: str) -> bool:
        if not self.is_grasped or self.held_handle is None:
            self.say('Gripper khali hai.', state='error')
            return False
        tip = center + front * C.GRASP_DEPTH + np.array([0, 0, C.GRASP_Z_OFFSET])
        pre = tip + front * C.PRE_GRASP_OFFSET + np.array([0, 0, 0.03])
        for name, pt in (('pre-place', pre), ('place', tip)):
            if arm_ik(self.world_to_base(pt), 0.0) is None:
                self.say(f'{label}: {name} point reach se bahar.', state='error')
                return False
        self.say(f'{label} pe book rakh raha hoon.', state='placing', progress=0.8)
        if not (self.arm_move_world(pre, 1.6) and self.arm_move_world(tip, 1.0)):
            return False
        h = self.held_handle
        self._unweld(h)
        self.gripper(C.GRIP_OPEN, wait=0.5)
        self.is_grasped, self.held_book, self.held_handle = False, None, None
        self.step(C.SETTLE_STEPS)
        pf = np.array(self.sim.getObjectPosition(h, -1))
        ok = bool(np.linalg.norm(pf[:2] - center[:2]) < 0.10 and abs(pf[2] - center[2]) < 0.06)
        self.say(f'Book ruki ({pf[0]:.2f}, {pf[1]:.2f}, {pf[2]:.2f}).' if ok else
                 f'Warning: book expected {np.round(center, 2)} par mili {np.round(pf, 2)}.')
        self.arm_move_world(pre, 0.9)
        self.arm_carry()
        return ok

    def place_on_table(self) -> bool:
        pt = self._table_center()
        f = self._front('Drop_Table')
        side = np.array([-f[1], f[0], 0.0])
        inset = C.TABLE_SIZE[0] / 2 - C.TABLE_INSET
        top = C.TABLE_TOP_Z
        xy = pt[:2] + f[:2] * inset + side[:2] * self._slot_lateral()
        center = np.array([xy[0], xy[1], top + C.BOOK_SIZE[2] / 2 + C.RELEASE_GAP])
        ok = self._place(center, f, 'Table')
        self._slot = (self._slot + 1) % len(C.TABLE_SLOTS)
        self.say('Placement complete.' if ok else 'Placement verify nahi hua.',
                 state='idle' if ok else 'error', progress=1.0 if ok else 0.0)
        return ok

    def shelve_book(self, code: str) -> bool:
        h = self.find(code)
        if h == -1:
            self.say(f"'{code}' table pe nahi mili.", state='error')
            return False
        f = self._front('Drop_Table')
        p = self.sim.getObjectPosition(h, -1)
        x, y, yaw = self._stand(p, f, C.PICK_TABLE_STANDOFF)
        if not self.navigate(x, y, yaw):
            return False
        if not self._pick(code, h, f):
            return False
        if not self.go_to_return_shelf():
            return False
        hs = self.find(C.RETURN_SHELF)
        ps = self.sim.getObjectPosition(hs, -1)
        center = np.array([ps[0], ps[1], C.RETURN_SHELF_PLACE_Z])
        ok = self._place(center, self._front(C.RETURN_SHELF), 'Cupboard_2')
        self.say(f"'{code}' return shelf pe." if ok else 'Shelving fail.',
                 state='idle' if ok else 'error', progress=1.0 if ok else 0.0)
        return ok

    def go_home(self):
        self.arm_carry(1.2)
        self.say('Station pe.', state='idle')

    # ----------------------------------------------------------- calibration
    def camera_image(self):
        """Gripper camera ka frame (RGB uint8 array) ya None."""
        if not self.cam:
            return None
        img, res = self.sim.getVisionSensorImg(self.cam)
        return np.flipud(np.frombuffer(img, np.uint8).reshape(res[1], res[0], 3))

    def calibrate_base(self) -> bool:
        """Forward/strafe/rotate asli sim me chalakar DRIVE_SIGNS verify aur auto-fix.
        Khali jagah chahiye (~1 m)."""
        def probe(vx, vy, wz, dur=1.5):
            x0, y0, t0 = self.pose()
            for _ in range(int(dur / self.dt)):
                self._drive_step(vx, vy, wz)
            self.stop_base()
            x1, y1, t1 = self.pose()
            d = np.array([x1 - x0, y1 - y0])
            return (float(d @ [math.cos(t0), math.sin(t0)]),
                    float(d @ [-math.sin(t0), math.cos(t0)]), wrap(t1 - t0))
        ok, flipped = True, False
        print('\n  command         forward      left     rotate')
        for nm, cmd, idx, thr in (('forward', (0.25, 0, 0), 0, 0.05), ('strafe-left', (0, 0.2, 0), 1, 0.05),
                                  ('rotate-ccw', (0, 0, 0.8), 2, 0.2)):
            r = probe(*cmd)
            if abs(r[idx]) >= thr and r[idx] < 0:
                sg = list(C.DRIVE_SIGNS)
                sg[idx] = -sg[idx]
                C.DRIVE_SIGNS = sg
                flipped = True
                print(f'  {nm:<13} {r[0]:+8.3f} {r[1]:+9.3f} {r[2]:+9.3f}   ULTA -> sign flip, dobara test')
                r = probe(*cmd)
            good = r[idx] > thr
            ok &= good
            print(f'  {nm:<13} {r[0]:+8.3f} {r[1]:+9.3f} {r[2]:+9.3f}   {"OK" if good else "WRONG"}')
        if flipped:
            print(f'\n  config.py me ye likh do (permanent): DRIVE_SIGNS = {[float(v) for v in C.DRIVE_SIGNS]}')
        if not ok:
            self.say('Calibration fail: wheel kaam nahi kar rahe. Wheels dynamic motor-enabled hai? '
                     'build_scene.py dobara chalao.', state='error')
        else:
            self.say('Calibration OK.', state='idle')
        return ok


YouBot = LibBot          # purane imports ke liye
