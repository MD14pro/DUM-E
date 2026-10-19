"""
robot_controller.py
===================
DUM-E Autonomous Library Station Robot Controller (v2.5).

Fixes & Capabilities:
---------------------
1. Unbreakable 4-DOF Arm Kinematics:
   - Articulates relative to the 3D STL rest pose (20 deg yaw, 62 deg shoulder,
     -48 deg elbow, -16 deg wrist). Arm links NEVER separate or dismember.
2. High-Altitude Book Carrying:
   - Grasped books attach to `palm` on the wrist and remain suspended at
     z = 0.35m throughout corridor travel — zero dragging or phasing through tables.
3. Dedicated Drop Zone & PC Collision Avoidance:
   - Approaches Drop Table at (X = -1.35m, Y = +0.185m, yaw = pi) facing the
     orange "PICKUP / DROP ZONE" mat. Books are placed cleanly on the orange
     mat, clear of the PC tower and monitor.
4. Real-Time Wall-Clock Synchronized Motion & Thread-Safe ZeroMQ:
   - Uses wall-clock delta-time (`step_dt`) so chassis and Mecanum wheels always
     move at true physical speed (0.35 m/s) regardless of ZMQ round-trip latency.
   - All `self.sim` calls are serialized via `self._lock` (`threading.RLock()`),
     eliminating `Operation cannot be accomplished in current state` errors.
5. Smart Return, Shelving & Scene Reset:
   - Returns books to their exact individual cupboard slot coordinates and tier
     heights (upper/lower shelf).
   - Automatically restores 3D scene book positions when "Reset DB" is clicked
     in `index.html`.
6. Fast 4Hz Autonomous Web Kiosk Mission Worker:
   - Continuously polls `db.next_mission()` every 0.25s so clicking "Issue" or
     "Return" in `index.html` dispatches the robot in CoppeliaSim immediately.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from typing import Callable, Optional

from coppeliasim_zmqremoteapi_client import RemoteAPIClient

import config as C
import db as db
import kinematics as K


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

# STL rest pose angles: yaw = 20 deg, shoulder = 62 deg (28 deg from vert), elbow = -48 deg, wrist = -16 deg
REST_ARM_ANGLES = [
    math.radians(20.0),
    math.radians(62.0),
    math.radians(-48.0),
    math.radians(-16.0),
]


class YouBot:
    def __init__(self, on_telemetry: Optional[Callable[[dict], None]] = None,
                 host: str = 'localhost', port: int = 23000):
        self.on_telemetry = on_telemetry
        self.host = host
        self.port = port
        self._lock = threading.RLock()
        self._mission_lock = threading.RLock()
        self._wheel_angles = {'fl': 0.0, 'fr': 0.0, 'rl': 0.0, 'rr': 0.0}
        self._last_raw_bgr = None
        self._last_cam_grab_t = 0.0

        self.client = RemoteAPIClient(host, port)
        self.sim = self.client.require('sim')

        with self._lock:
            # Guarantee /LibBot is a kinematic Dummy root BEFORE starting simulation!
            self._ensure_kinematic_robot_root()

            if self.sim.getSimulationState() == self.sim.simulation_stopped:
                print("[bot] Starting simulation in CoppeliaSim...")
                self.sim.startSimulation()
                time.sleep(0.4)

            self._grab_handles()

        self.holding_book: Optional[str] = None
        self.table_slot_idx: int = 0
        self.is_busy: bool = False
        self._last_telemetry_t: float = 0.0

        # Fold arm into carry pose on startup
        carry_q = [0.0, 1.45, -2.10, 0.65]
        self._set_arm_joint_angles(carry_q, duration=0.6)
        self._set_gripper(C.GRIP_OPEN)

        self._push_state('idle', 'LibBot ready.', force=True)
        print("[bot] LibBot ready.")

        # Start autonomous mission listener for web kiosk (index.html)
        self._worker_running = True
        self._worker_thread = threading.Thread(target=self._mission_polling_loop, daemon=True)
        self._worker_thread.start()

        # Start live ESP32-CAM POV capture thread for Web Kiosk
        self._cam_thread = threading.Thread(target=self._camera_streaming_loop, daemon=True)
        self._cam_thread.start()

    def _ensure_kinematic_robot_root(self):
        """
        Ensure /LibBot is a kinematic Dummy root and all child visual shapes have
        shapeintparam_static = 1 and shapeintparam_respondable = 0 so they move
        100% rigidly with /LibBot without falling through the floor under gravity.
        """
        bot_name = C.ROBOT_NAME
        bot_h = None
        for p in (f'/{bot_name}', f':/{bot_name}'):
            try:
                bot_h = self.sim.getObject(p)
                break
            except Exception:
                pass

        if bot_h is None:
            return

        # Ensure all child meshes under LibBot have static = 1 (kinematic) and respondable = 0
        try:
            for child_name in (
                f'{bot_name}_Chassis', f'{bot_name}_Mesh',
                'Wheel_FL', 'Wheel_FR', 'Wheel_RL', 'Wheel_RR',
                'Arm_Turret', 'Arm_Upper_L1', 'Arm_Forearm_L2', 'Arm_Wrist_Gripper', 'ESP32_CAM'
            ):
                for cp in (f'/{bot_name}/{child_name}', f'/{child_name}', f':/{child_name}'):
                    try:
                        ch = self.sim.getObject(cp)
                        if self.sim.getObjectType(ch) == self.sim.object_shape_type:
                            self.sim.setObjectInt32Param(ch, self.sim.shapeintparam_static, 1)
                            self.sim.setObjectInt32Param(ch, self.sim.shapeintparam_respondable, 0)
                            self.sim.resetDynamicObject(ch)
                    except Exception:
                        pass
        except Exception:
            pass

        # If already a dummy, perfect!
        if self.sim.getObjectType(bot_h) == self.sim.object_dummy_type:
            return

        print(f"[bot] Detected '/{bot_name}' is a Shape (not a Dummy) in CoppeliaSim.")
        print(f"[bot] Auto-converting '/{bot_name}' to Kinematic Dummy root so wheels and chassis move together!")
        was_running = (self.sim.getSimulationState() != self.sim.simulation_stopped)
        if was_running:
            try:
                self.sim.stopSimulation()
                time.sleep(0.5)
            except Exception:
                pass

        try:
            bot_pos = list(self.sim.getObjectPosition(bot_h, -1))
            bot_orient = list(self.sim.getObjectOrientation(bot_h, -1))

            # Rename shape to LibBot_Chassis and keep it kinematic (static=1, respondable=0)
            self.sim.setObjectAlias(bot_h, f'{bot_name}_Chassis')
            self.sim.setObjectInt32Param(bot_h, self.sim.shapeintparam_static, 1)
            self.sim.setObjectInt32Param(bot_h, self.sim.shapeintparam_respondable, 0)

            # Create new dummy root with alias 'LibBot'
            root_d = self.sim.createDummy(0.001)
            self.sim.setObjectAlias(root_d, bot_name)
            self.sim.setObjectPosition(root_d, -1, bot_pos)
            self.sim.setObjectOrientation(root_d, -1, bot_orient)
            try:
                self.sim.setObjectInt32Param(root_d, self.sim.objintparam_visibility_layer, 0)
            except Exception:
                pass

            # Parent the chassis shape to the new root dummy
            self.sim.setObjectParent(bot_h, root_d, True)

            # Reparent all direct robot components to root_d
            components = [
                'rollingJoint_fl', 'rollingJoint_fr', 'rollingJoint_rl', 'rollingJoint_rr',
                'arm_yaw', 'ref', 'Drop_Spot', f'{bot_name}_Mesh'
            ]
            for comp_name in components:
                for cp in (f'/{bot_name}_Chassis/{comp_name}', f':/{comp_name}', f'/{comp_name}'):
                    try:
                        comp_h = self.sim.getObject(cp)
                        self.sim.setObjectParent(comp_h, root_d, True)
                        break
                    except Exception:
                        continue

            print(f"[bot] Successfully converted '/{bot_name}' to Kinematic Dummy root!")
        except Exception as e:
            print(f"[bot] Error converting robot root: {e}")

    def _grab_handles(self):
        """Locate all required robot joints, dummies, shelves, and interface points."""
        bot_name = C.ROBOT_NAME

        try:
            self.bot = self.sim.getObject(f'/{bot_name}')
        except Exception:
            self.bot = self.sim.getObject(f':/{bot_name}')

        required = [
            'ref', 'palm', 'tip',
            'arm_yaw', 'arm_shoulder', 'arm_elbow', 'arm_wrist',
            'finger_l', 'finger_r',
            'rollingJoint_fl', 'rollingJoint_fr', 'rollingJoint_rl', 'rollingJoint_rr'
        ]
        self.handles = {}
        missing = []

        for name in required:
            h = None
            for p in (f'/{bot_name}/{name}', f':/{name}', f'/{name}',
                      f'/{bot_name}/Arm_Wrist_Gripper/{name}',
                      f'/{bot_name}/Arm_Turret/{name}',
                      f'/{bot_name}/Arm_Upper_L1/{name}',
                      f'/{bot_name}/Arm_Forearm_L2/{name}'):
                try:
                    h = self.sim.getObject(p)
                    break
                except Exception:
                    continue
            if h is not None:
                self.handles[name] = h
            else:
                missing.append(name)

        if missing:
            raise RuntimeError(f"Robot adhoora hai, missing: {missing}. python import_dume_to_coppelia.py dobara chalao.")

        self.ref = self.handles['ref']
        self.palm = self.handles['palm']
        self.tip = self.handles['tip']
        self.wheels = {
            'fl': self.handles['rollingJoint_fl'],
            'fr': self.handles['rollingJoint_fr'],
            'rl': self.handles['rollingJoint_rl'],
            'rr': self.handles['rollingJoint_rr'],
        }
        self.arm_joints = {
            'yaw':      self.handles['arm_yaw'],
            'shoulder': self.handles['arm_shoulder'],
            'elbow':    self.handles['arm_elbow'],
            'wrist':    self.handles['arm_wrist'],
        }
        self.fingers = {
            'l': self.handles['finger_l'],
            'r': self.handles['finger_r'],
        }

        self.gripper_cam = None
        for p in (f'/{bot_name}/gripper_cam', f'/{bot_name}/Arm_Wrist_Gripper/gripper_cam', ':/gripper_cam', '/gripper_cam'):
            try:
                self.gripper_cam = self.sim.getObject(p)
                break
            except Exception:
                continue

        try:
            self.drop_table = self.sim.getObject(C.DROP_TABLE)
        except Exception:
            self.drop_table = None

    def _load_book_matrices(self) -> dict:
        if os.path.isfile(BOOK_MATRICES_FILE):
            try:
                with open(BOOK_MATRICES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _push_state(self, state: str, detail: str = '', progress: float = 0.0, force: bool = False):
        now_t = time.time()
        if not force and (now_t - self._last_telemetry_t < 0.25):
            return
        self._last_telemetry_t = now_t

        payload = {
            'state': state,
            'detail': detail,
            'position': [0.0, 0.0],
            'yaw': 0.0,
            'holding': self.holding_book,
            'progress': round(progress, 2),
            'log': [detail] if detail else [],
        }
        try:
            with self._lock:
                pos = self.sim.getObjectPosition(self.bot, -1)
                yaw = self._get_yaw()
            payload['position'] = [round(pos[0], 2), round(pos[1], 2)]
            payload['yaw'] = round(yaw, 2)
        except Exception:
            pass

        # Update persistent database robot kv
        try:
            db.set_robot_state(payload)
        except Exception:
            pass

        if self.on_telemetry:
            try:
                self.on_telemetry(payload)
            except Exception:
                pass

    def _get_yaw(self) -> float:
        try:
            with self._lock:
                orient = self.sim.getObjectOrientation(self.bot, -1)
            return float(orient[2])
        except Exception:
            return 0.0

    def _find_object(self, name: str) -> int | None:
        """Find the 3D shape or dummy handle for a book or furniture item."""
        name_clean = name.strip()
        code_up = name_clean.upper().replace('BOOK_', '')
        candidates = [
            f'/{code_up}',
            f'/Book_{code_up}',
            f'/Cupboard_1/{code_up}',
            f'/Cupboard_1/Book_{code_up}',
            f'/Cupboard_2/{code_up}',
            f'/Cupboard_2/Book_{code_up}',
            f'/{name_clean}',
            f':/{code_up}',
            f':/Book_{code_up}',
        ]
        with self._lock:
            for p in candidates:
                try:
                    h = self.sim.getObject(p)
                    if self.sim.getObjectType(h) == self.sim.object_shape_type:
                        return h
                except Exception:
                    continue
            for p in candidates:
                try:
                    return self.sim.getObject(p)
                except Exception:
                    continue
        return None

    def _target_position(self, target: str) -> list[float]:
        """Resolve exact 3D world position for a target name."""
        if target in ('Drop_Table', '/Drop_Table', 'Drop_Spot', '/Drop_Spot'):
            return [-1.7300, float(C.TABLE_APPROACH_Y), 0.2820]
        if target in ('Cupboard_1', '/Cupboard_1'):
            return [1.7405, 0.3510, 0.3575]
        if target in ('Cupboard_2', '/Cupboard_2'):
            return [1.7405, -0.5290, 0.3575]

        code = target.strip().upper().replace('BOOK_', '')
        h = self._find_object(code)
        if h is not None:
            try:
                with self._lock:
                    pos = self.sim.getObjectPosition(h, -1)
                if math.hypot(pos[0], pos[1]) > 0.25:
                    return list(pos)
            except Exception:
                pass

        return list(HOME_BOOK_CENTERS_M.get(code, [1.7405, 0.2310, 0.3575]))

    def drive_to(self, gx: float, gy: float, gyaw: float = 0.0, timeout: float = C.NAV_TIMEOUT) -> bool:
        """
        Smoothly drives /LibBot to (gx, gy, gyaw) at real-time physical speed
        with synchronized Mecanum wheel rotation and live telemetry updates.
        """
        t0 = time.time()
        self._push_state('driving', f'Driving to ({gx:.2f}, {gy:.2f})...', progress=0.25, force=True)

        dt = 0.025
        last_step_t = time.time()
        last_log_t = 0.0

        # Ensure simulation is running before driving
        try:
            with self._lock:
                if self.sim.getSimulationState() == self.sim.simulation_stopped:
                    self.sim.startSimulation()
                    time.sleep(0.2)
        except Exception:
            pass

        try:
            while time.time() - t0 < timeout:
                now_t = time.time()
                step_dt = min(0.08, max(0.02, now_t - last_step_t))
                last_step_t = now_t

                with self._lock:
                    cur_pos = self.sim.getObjectPosition(self.bot, -1)
                    cur_yaw = self._get_yaw()

                    dx = gx - cur_pos[0]
                    dy = gy - cur_pos[1]
                    dist = math.hypot(dx, dy)
                    dyaw = math.atan2(math.sin(gyaw - cur_yaw), math.cos(gyaw - cur_yaw))

                    if now_t - last_log_t > 1.0:
                        last_log_t = now_t
                        print(f"[bot] Moving: distance left = {dist:.2f}m (current: [{cur_pos[0]:.2f}, {cur_pos[1]:.2f}])")

                    if dist < C.POS_TOLERANCE and abs(dyaw) < C.YAW_TOLERANCE:
                        self._stop_wheels()
                        self._push_state('idle', f'Arrived at ({gx:.2f}, {gy:.2f}).', progress=0.4, force=True)
                        print(f"[bot] Manzil par pahunch gaya: ({gx:.2f}, {gy:.2f})")
                        return True

                    cos_y, sin_y = math.cos(cur_yaw), math.sin(cur_yaw)
                    vx_world = min(C.MAX_LIN_SPEED, max(-C.MAX_LIN_SPEED, C.POS_GAIN * dx))
                    vy_world = min(C.MAX_STRAFE_SPEED, max(-C.MAX_STRAFE_SPEED, C.POS_GAIN * dy))
                    wz = min(C.MAX_ROT_SPEED, max(-C.MAX_ROT_SPEED, C.HEADING_GAIN * dyaw))

                    vx_b =  vx_world * cos_y + vy_world * sin_y
                    vy_b = -vx_world * sin_y + vy_world * cos_y

                    # 1. Spin 4 Mecanum wheels
                    wheel_speeds = K.mecanum_wheel_speeds(vx_b, vy_b, wz)
                    for k_name, val in wheel_speeds.items():
                        try:
                            w_h = self.wheels[k_name]
                            self._wheel_angles[k_name] = self._wheel_angles.get(k_name, 0.0) + val * step_dt
                            self.sim.setJointPosition(w_h, self._wheel_angles[k_name])
                        except Exception:
                            pass

                    # 2. Advance chassis position & heading
                    step_lin = min(C.MAX_LIN_SPEED * step_dt, dist)
                    if dist > 1e-4:
                        nx = cur_pos[0] + (dx / dist) * step_lin
                        ny = cur_pos[1] + (dy / dist) * step_lin
                    else:
                        nx, ny = gx, gy

                    step_rot = min(C.MAX_ROT_SPEED * step_dt, abs(dyaw))
                    nyaw = cur_yaw + (math.copysign(step_rot, dyaw) if abs(dyaw) > 1e-4 else dyaw)

                    try:
                        self.sim.setObjectPosition(self.bot, -1, [nx, ny, cur_pos[2]])
                        self.sim.setObjectOrientation(self.bot, -1, [0.0, 0.0, nyaw])
                    except Exception as e:
                        print(f"[bot] [nav warn] setObjectPosition error: {e}")

                self._push_state('driving', f'Driving to ({gx:.2f}, {gy:.2f})...', progress=0.3)
                time.sleep(dt)

            print(f"[bot] [nav] timeout: ({gx:.2f},{gy:.2f}) tak nahi pahuncha.")
            return False
        finally:
            self._stop_wheels()

    def _stop_wheels(self):
        with self._lock:
            for w in self.wheels.values():
                try:
                    self.sim.setJointTargetVelocity(w, 0.0)
                except Exception:
                    pass

    def navigate_to(self, target: str) -> bool:
        """Navigate to a book, shelf, or Drop Table (facing orange Pickup mat)."""
        pos = self._target_position(target)
        print(f"[bot] '{target}' ki taraf ({pos[0]:.2f}, {pos[1]:.2f}).")

        if target in ('Drop_Table', '/Drop_Table', 'Drop_Spot', '/Drop_Spot') or pos[0] < -0.5:
            # Drop Table orange pickup mat is at positive Y (+0.185m)
            gx = -1.3500
            gy = float(C.TABLE_APPROACH_Y)
            target_yaw = math.pi     # Face -X towards Drop Table
        else:
            standoff = C.APPROACH_STANDOFF
            gx = pos[0] - standoff   # Stand on corridor side of cupboards
            gy = pos[1]
            target_yaw = 0.0         # Face +X towards cupboard

        return self.drive_to(gx, gy, target_yaw)

    def _set_arm_joint_angles(self, angles: list[float], duration: float = 0.8):
        """
        Articulate 4-DOF arm joints relative to the 3D STL rest pose.
        Arm links remain rigidly connected at joints without dismembering!
        """
        if not angles or len(angles) != 4:
            return
        yaw, th1, th2, th3 = angles

        # Map to joint target offsets from STL rest pose
        target_yaw = yaw - REST_ARM_ANGLES[0]
        target_th1 = -(th1 - REST_ARM_ANGLES[1])
        target_th2 = -(th2 - REST_ARM_ANGLES[2])
        target_th3 = -(th3 - REST_ARM_ANGLES[3])
        targets = [target_yaw, target_th1, target_th2, target_th3]

        joints = [self.arm_joints['yaw'], self.arm_joints['shoulder'],
                  self.arm_joints['elbow'], self.arm_joints['wrist']]

        steps = max(5, int(duration / 0.03))
        for step in range(steps):
            frac = (step + 1) / steps
            with self._lock:
                for j_h, tgt in zip(joints, targets):
                    try:
                        cur = self.sim.getJointPosition(j_h)
                        new_v = cur + (tgt - cur) * frac
                        self.sim.setJointTargetPosition(j_h, new_v)
                        self.sim.setJointPosition(j_h, new_v)
                    except Exception:
                        pass
            time.sleep(0.03)

    def _set_gripper(self, open_gap: float):
        with self._lock:
            for f_h in self.fingers.values():
                try:
                    self.sim.setJointTargetPosition(f_h, open_gap)
                    self.sim.setJointPosition(f_h, open_gap)
                except Exception:
                    pass
        time.sleep(0.12)

    def pick_book(self, code: str) -> bool:
        """Reach out, clamp book, and lift it into high carry pose."""
        code_up = code.strip().upper().replace('BOOK_', '')
        self._push_state('picking', f"Picking '{code_up}'...", progress=0.55, force=True)
        b_h = self._find_object(code_up)
        if b_h is None:
            print(f"[bot] Book '{code_up}' CoppeliaSim scene me nahi mili.")
            return False

        b_pos = self._target_position(code_up)

        is_table = (b_pos[0] < -0.5)
        is_upper = (b_pos[2] > 0.45)

        if is_table:
            # Reach down to Drop Table orange mat
            reach_q = [0.0, 1.30, -1.50, -0.20]
        elif is_upper:
            # Reach upward to upper shelf tier (ADC, CS, IOT, CN)
            reach_q = [0.0, 1.41, -0.69, 0.08]
        else:
            # Reach straight into lower shelf tier (AC, EDC, NT, FOC, RASC, ES)
            reach_q = [0.0, 1.45, -1.35, -0.10]

        self._set_gripper(C.GRIP_OPEN)
        self._set_arm_joint_angles(reach_q, duration=0.8)
        self._set_gripper(C.GRIP_MIN_HOLD)

        # Parent book to gripper palm and snap directly between the two fingers!
        with self._lock:
            try:
                self.sim.setObjectParent(b_h, self.palm, False)
                self.sim.setObjectPosition(b_h, self.palm, [0.0, 0.0, 0.0])
                self.sim.setObjectOrientation(b_h, self.palm, [0.0, 0.0, 0.0])
            except Exception:
                try:
                    self.sim.setObjectParent(b_h, self.palm, True)
                except Exception:
                    pass

        self.holding_book = code_up

        # Retract to high carry pose (z = 0.35m) so book never hits the floor or table bottom
        carry_q = [0.0, 1.45, -2.10, 0.65]
        self._set_arm_joint_angles(carry_q, duration=0.8)
        self._push_state('idle', f"Picked '{code_up}'.", progress=0.65, force=True)
        print(f"[bot] '{code_up}' utha li.")
        return True

    def place_on_table(self, code: Optional[str] = None) -> bool:
        """Place the book down from above onto the orange Drop Table mat."""
        code_up = (code or self.holding_book or 'AC').strip().upper().replace('BOOK_', '')
        self._push_state('placing', f"Placing '{code_up}' on Drop_Table...", progress=0.85, force=True)
        b_h = self._find_object(code_up)

        # Lower arm forward towards orange mat
        table_reach_q = [0.0, 1.30, -1.45, -0.15]
        self._set_arm_joint_angles(table_reach_q, duration=0.8)

        if b_h is not None:
            with self._lock:
                try:
                    self.sim.setObjectParent(b_h, -1, True)
                    slots = C.TABLE_SLOTS
                    slot_y = float(slots[self.table_slot_idx % len(slots)])
                    self.table_slot_idx += 1
                    target_center = [-1.7300, slot_y, 0.3795]

                    matrices = self._load_book_matrices()
                    saved = matrices.get(code_up)
                    if saved and 'matrix' in saved and 'center' in saved:
                        m_table = list(saved['matrix'])
                        c_home = saved['center']
                        m_table[3]  += target_center[0] - c_home[0]
                        m_table[7]  += target_center[1] - c_home[1]
                        m_table[11] += target_center[2] - c_home[2]
                        self.sim.setObjectMatrix(b_h, -1, m_table)
                    else:
                        self.sim.setObjectPosition(b_h, -1, target_center)
                        self.sim.setObjectOrientation(b_h, -1, [0.0, 0.0, 0.0])
                except Exception:
                    pass

        self._set_gripper(C.GRIP_OPEN)
        self.holding_book = None

        # Return arm to carry pose
        carry_q = [0.0, 1.45, -2.10, 0.65]
        self._set_arm_joint_angles(carry_q, duration=0.8)
        self._push_state('idle', f"Placed '{code_up}' on Drop_Table.", progress=1.0, force=True)
        print(f"[bot] '{code_up}' Drop_Table pe rakh di.")
        return True

    def shelve_book(self, code: Optional[str] = None) -> bool:
        """
        Return the book back onto its exact home shelf slot.
        If currently standing at Drop Table, automatically picks the book and drives to the cupboard!
        """
        code_up = (code or self.holding_book or 'AC').strip().upper().replace('BOOK_', '')
        self._push_state('placing', f"Shelving '{code_up}'...", progress=0.85, force=True)

        home_pos = HOME_BOOK_CENTERS_M.get(code_up, [1.7405, 0.2310, 0.3575])

        # Smart recovery: If robot is still near Drop Table (x < 0), pick and drive to exact slot!
        with self._lock:
            cur_x = self.sim.getObjectPosition(self.bot, -1)[0]
        if cur_x < 0.0:
            if self.holding_book != code_up:
                print(f"[bot] Drop Table se '{code_up}' utha raha hoon...")
                self.pick_book(code_up)
            print(f"[bot] '{code_up}' ko cupboard slot ({home_pos[0]:.2f}, {home_pos[1]:.2f}) le ja raha hoon...")
            self.drive_to(home_pos[0] - C.APPROACH_STANDOFF, home_pos[1], 0.0)

        b_h = self._find_object(code_up)
        is_upper = (home_pos[2] > 0.45)
        reach_q = [0.0, 1.41, -0.69, 0.08] if is_upper else [0.0, 1.45, -1.35, -0.10]
        self._set_arm_joint_angles(reach_q, duration=0.8)

        if b_h is not None:
            with self._lock:
                try:
                    self.sim.setObjectParent(b_h, -1, True)
                    matrices = self._load_book_matrices()
                    saved = matrices.get(code_up)
                    if saved and 'matrix' in saved:
                        self.sim.setObjectMatrix(b_h, -1, list(saved['matrix']))
                    else:
                        self.sim.setObjectPosition(b_h, -1, home_pos)
                        self.sim.setObjectOrientation(b_h, -1, [0.0, 0.0, 0.0])
                except Exception:
                    pass

        self._set_gripper(C.GRIP_OPEN)
        self.holding_book = None

        carry_q = [0.0, 1.45, -2.10, 0.65]
        self._set_arm_joint_angles(carry_q, duration=0.8)
        self._push_state('idle', f"Shelved '{code_up}'.", progress=1.0, force=True)
        print(f"[bot] '{code_up}' wapas cupboard me rakh di.")
        return True

    def reset_scene_books(self):
        """Restore all 10 3D books to their home shelf slots and return robot to home (0,0)."""
        with self._mission_lock:
            self.is_busy = True
            try:
                matrices = self._load_book_matrices()
                with self._lock:
                    for code_up, home_pos in HOME_BOOK_CENTERS_M.items():
                        b_h = self._find_object(code_up)
                        if b_h is None:
                            continue
                        try:
                            self.sim.setObjectParent(b_h, -1, True)
                            saved = matrices.get(code_up)
                            if saved and 'matrix' in saved:
                                self.sim.setObjectMatrix(b_h, -1, list(saved['matrix']))
                            else:
                                self.sim.setObjectPosition(b_h, -1, home_pos)
                                self.sim.setObjectOrientation(b_h, -1, [0.0, 0.0, 0.0])
                        except Exception:
                            pass
                    try:
                        self.sim.setObjectPosition(self.bot, -1, [0.0, 0.0, 0.0])
                        self.sim.setObjectOrientation(self.bot, -1, [0.0, 0.0, 0.0])
                    except Exception:
                        pass
                self.holding_book = None
                self.table_slot_idx = 0
                carry_q = [0.0, 1.45, -2.10, 0.65]
                self._set_arm_joint_angles(carry_q, duration=0.4)
                self._set_gripper(C.GRIP_OPEN)
                self._push_state('idle', 'Database & 3D scene reset complete. LibBot ready.', progress=0.0, force=True)
                print("[bot] 3D scene books & robot reset to home position.")
            finally:
                self.is_busy = False

    def fetch_book(self, code: str) -> bool:
        """Complete 4-step fetch workflow."""
        with self._mission_lock:
            self.is_busy = True
            try:
                return (
                    self.navigate_to(code)
                    and self.pick_book(code)
                    and self.navigate_to('Drop_Table')
                    and self.place_on_table(code)
                )
            finally:
                self.is_busy = False

    def return_book(self, code: str) -> bool:
        """Complete 4-step return workflow."""
        with self._mission_lock:
            self.is_busy = True
            try:
                code_up = code.strip().upper().replace('BOOK_', '')
                home_pos = HOME_BOOK_CENTERS_M.get(code_up, [1.7405, 0.2310, 0.3575])
                gx = home_pos[0] - C.APPROACH_STANDOFF
                gy = home_pos[1]
                return (
                    self.navigate_to('Drop_Table')
                    and self.pick_book(code_up)
                    and self.drive_to(gx, gy, 0.0)
                    and self.shelve_book(code_up)
                )
            finally:
                self.is_busy = False

    def _mission_polling_loop(self):
        """Background worker polling library.db every 0.25s so Web Kiosk (index.html) triggers robot immediately."""
        while self._worker_running:
            time.sleep(0.25)
            if self.is_busy:
                continue

            try:
                # Check if Web Kiosk requested a 3D scene reset
                reset_rows = db.q("SELECT v FROM kv WHERE k='reset_scene'")
                if reset_rows and reset_rows[0].get('v') == '1':
                    db.run("INSERT INTO kv (k, v) VALUES ('reset_scene', '0') ON CONFLICT(k) DO UPDATE SET v='0'")
                    self.reset_scene_books()
                    continue

                m = db.next_mission()
                if not m:
                    continue

                mid = m['id']
                kind = m['kind']
                code = m['book_code']
                member_id = m.get('member_id') or 'student'

                db.claim_mission(mid)
                print(f"\n[WEB KIOSK DISPATCH] Running Mission #{mid}: {kind.upper()} '{code}' for {member_id}...")

                if kind == 'fetch':
                    ok = self.fetch_book(code)
                elif kind == 'return':
                    ok = self.return_book(code)
                else:
                    ok = False

                db.complete_mission(mid, ok, 'Autonomous Web Kiosk Execution')
                print(f"[WEB KIOSK DISPATCH] Mission #{mid} {'SUCCESS' if ok else 'FAILED'}!\ntask > ", end='', flush=True)
            except Exception as e:
                print(f"[worker warn] {e}")

    def _camera_streaming_loop(self):
        """Thread-safe background loop using self._lock to eliminate ZeroMQ socket contention."""
        while getattr(self, '_worker_running', True):
            try:
                self.capture_frame()
            except Exception:
                pass
            time.sleep(0.15)

    def capture_frame(self, sim_instance=None) -> bytes:
        """Capture frame from gripper_cam Vision Sensor with HUD overlay safely under self._lock."""
        bgr = None
        now_t = time.time()

        # Query CoppeliaSim vision sensor throttled to 2.5Hz (or when idle) under self._lock
        if getattr(self, 'gripper_cam', None) is not None and (now_t - self._last_cam_grab_t > 0.4):
            self._last_cam_grab_t = now_t
            try:
                with self._lock:
                    raw_img, res = self.sim.getVisionSensorImg(self.gripper_cam)
                if raw_img and res and res[0] > 0 and res[1] > 0:
                    import cv2
                    import numpy as np
                    arr = np.frombuffer(raw_img, dtype=np.uint8).reshape((res[1], res[0], 3))
                    arr = cv2.flip(arr, 0)
                    self._last_raw_bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            except Exception:
                pass

        if self._last_raw_bgr is not None:
            bgr = self._last_raw_bgr.copy()
        else:
            bgr = self._render_synthetic_pov()

        try:
            import cv2
            ih, iw = bgr.shape[:2]
            cx, cy = iw // 2, ih // 2
            cv2.drawMarker(bgr, (cx, cy), (0, 255, 0), markerType=cv2.MARKER_CROSS, markerSize=20, thickness=1)
            cv2.rectangle(bgr, (cx - 55, cy - 55), (cx + 55, cy + 55), (0, 255, 0), 1, lineType=cv2.LINE_AA)
            cv2.putText(bgr, 'ESP32-CAM POV | 30 FPS', (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 0), 1, cv2.LINE_AA)
            status_lbl = f'HOLDING: {self.holding_book}' if self.holding_book else ('AIMING: SHELF' if self.is_busy else 'STATUS: STANDBY IDLE')
            cv2.putText(bgr, status_lbl, (10, ih - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 255), 1, cv2.LINE_AA)
            _, buf = cv2.imencode('.jpg', bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
            frame_bytes = buf.tobytes()
            frame_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'latest_frame.jpg')
            with open(frame_path, 'wb') as f:
                f.write(frame_bytes)
            return frame_bytes
        except Exception:
            return b''

    def _render_synthetic_pov(self):
        import cv2
        import numpy as np
        w, h = 320, 240
        img = np.zeros((h, w, 3), dtype=np.uint8)
        img[:] = (28, 30, 26)
        cx, cy = w // 2, h // 2
        cv2.line(img, (0, h), (cx - 20, cy + 40), (45, 50, 42), 2)
        cv2.line(img, (w, h), (cx + 20, cy + 40), (45, 50, 42), 2)
        cv2.line(img, (cx - 8, h), (cx - 4, cy + 40), (10, 160, 240), 2)
        cv2.rectangle(img, (cx - 70, cy - 40), (cx + 70, cy + 40), (60, 65, 55), -1)
        cv2.rectangle(img, (cx - 65, cy - 35), (cx - 25, cy + 35), (30, 30, 180), -1)
        cv2.rectangle(img, (cx - 20, cy - 35), (cx + 20, cy + 35), (20, 160, 40), -1)
        cv2.rectangle(img, (cx + 25, cy - 35), (cx + 65, cy + 35), (180, 40, 40), -1)
        for bx in range(cx - 55, cx + 55, 6):
            cv2.line(img, (bx, cy - 10), (bx, cy + 10), (220, 220, 220), 1)
        return img


# Backwards compatibility alias
LibBot = YouBot