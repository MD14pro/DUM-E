"""
Pure kinematics - sim ke bina import/test ho sakta hai.

Arm planar hai (yaw ke baad 3 pitch joints), isliye IK closed-form hai:
koi iterative solver nahi, local minima nahi, hamesha same answer.
Angles: th1/th2/th3 floor se UPAR positive (planar angle). Sim joint ka
axis +y hai, jis par positive rotation neeche jaata hai, isliye
sim_q = -th. Ye conversion sirf robot_controller me hota hai.
"""
from __future__ import annotations
import math
from typing import Optional, Sequence
import numpy as np
import config as C


def arm_fk(q: Sequence[float]) -> np.ndarray:
    """(yaw, th1, th2, th3) -> grasp point, base frame."""
    yaw, t1, t2, t3 = q
    d = (C.ARM_L1 * math.cos(t1) + C.ARM_L2 * math.cos(t1 + t2)
         + C.ARM_L3 * math.cos(t1 + t2 + t3))
    z = (C.Z_SHOULDER + C.ARM_L1 * math.sin(t1) + C.ARM_L2 * math.sin(t1 + t2)
         + C.ARM_L3 * math.sin(t1 + t2 + t3))
    return np.array([d * math.cos(yaw), d * math.sin(yaw), z])


def elbow_point(q: Sequence[float]) -> np.ndarray:
    yaw, t1, _, _ = q
    d = C.ARM_L1 * math.cos(t1)
    return np.array([d * math.cos(yaw), d * math.sin(yaw),
                     C.Z_SHOULDER + C.ARM_L1 * math.sin(t1)])


def wrist_point(q: Sequence[float]) -> np.ndarray:
    yaw, t1, t2, _ = q
    d = C.ARM_L1 * math.cos(t1) + C.ARM_L2 * math.cos(t1 + t2)
    return np.array([d * math.cos(yaw), d * math.sin(yaw),
                     C.Z_SHOULDER + C.ARM_L1 * math.sin(t1) + C.ARM_L2 * math.sin(t1 + t2)])


def _in_limits(q) -> bool:
    for v, k in zip(q, ('yaw', 'th1', 'th2', 'th3')):
        lo, hi = C.ARM_LIMITS[k]
        if v < lo - 1e-6 or v > hi + 1e-6:
            return False
    return True


def arm_ik(p: Sequence[float], phi: float = 0.0) -> Optional[list]:
    """
    Grasp point p (base frame) aur gripper pitch phi (0 = horizontal) ke liye
    (yaw, th1, th2, th3). Elbow hamesha UPAR. Reach/limits ke bahar -> None.
    """
    x, y, z = float(p[0]), float(p[1]), float(p[2])
    yaw = math.atan2(y, x) if (abs(x) + abs(y)) > 1e-6 else 0.0
    d = math.hypot(x, y)
    wx = d - C.ARM_L3 * math.cos(phi)
    wz = (z - C.Z_SHOULDER) - C.ARM_L3 * math.sin(phi)
    r2 = wx * wx + wz * wz
    r = math.sqrt(r2)
    L1, L2 = C.ARM_L1, C.ARM_L2
    if r > L1 + L2 - 1e-3 or r < abs(L1 - L2) + 1e-3:
        return None
    c = (r2 - L1 * L1 - L2 * L2) / (2 * L1 * L2)
    m = math.acos(max(-1.0, min(1.0, c)))          # elbow ka andar ka mod
    th2 = -m
    th1 = math.atan2(wz, wx) + math.atan2(L2 * math.sin(m), L1 + L2 * math.cos(m))
    th3 = phi - th1 - th2
    q = [yaw, th1, th2, th3]
    return q if _in_limits(q) else None


def mecanum_wheel_speeds(vx: float, vy: float, wz: float) -> dict:
    """Body velocity (m/s, m/s, rad/s) -> har wheel ki angular velocity (rad/s).
    Sab wheel joints ka axis +y hai (forward = positive)."""
    k, R = C.MECANUM_K, C.WHEEL_RADIUS
    return {'fl': (vx - vy - k * wz) / R, 'fr': (vx + vy + k * wz) / R,
            'rl': (vx + vy - k * wz) / R, 'rr': (vx - vy + k * wz) / R}


def body_velocity_from_wheels(w: dict) -> tuple:
    """Inverse - calibration/test ke liye."""
    R, k = C.WHEEL_RADIUS, C.MECANUM_K
    vx = R * (w['fl'] + w['fr'] + w['rl'] + w['rr']) / 4
    vy = R * (-w['fl'] + w['fr'] + w['rl'] - w['rr']) / 4
    wz = R * (-w['fl'] + w['fr'] - w['rl'] + w['rr']) / (4 * k)
    return vx, vy, wz


def segment_hits_wall(p0, p1, margin: float = C.WALL_MARGIN) -> bool:
    for t in np.linspace(0.0, 1.0, max(2, int(math.dist(p0, p1) / 0.04))):
        x = p0[0] + (p1[0] - p0[0]) * t
        y = p0[1] + (p1[1] - p0[1]) * t
        for (x0, x1, y0, y1) in C.WALLS:
            if x0 - margin <= x <= x1 + margin and y0 - margin <= y <= y1 + margin:
                return True
    return False


def route(p0, p1) -> list:
    """Waypoints (p1 included). Seedha raasta deewar se takraye to HUB se ghumao."""
    if not segment_hits_wall(p0, p1):
        return [tuple(p1)]
    if not segment_hits_wall(p0, C.HUB) and not segment_hits_wall(C.HUB, p1):
        return [C.HUB, tuple(p1)]
    return [tuple(p1)]
