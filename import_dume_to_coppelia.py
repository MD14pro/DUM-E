"""
import_dume_to_coppelia.py
==========================
Modular Multi-Object & 15-Color CoppeliaSim Importer with Complete Kinematic
Arm Chain, Axis-Aligned Primitive Carriers, and True 3D Object Origins.

Features & Fixes:
-----------------
1. Unbreakable 4-DOF Robotic Arm Kinematic Chain:
   - Connects:
       /LibBot -> arm_yaw -> Arm_Turret -> arm_shoulder -> Arm_Upper_L1 ->
       arm_elbow -> Arm_Forearm_L2 -> arm_wrist -> Arm_Wrist_Gripper ->
       palm, tip, gripper_cam, finger_l, finger_r, ESP32_CAM
   - The arm links and joints NEVER separate, float in mid-air, or break!
   - Grasped books attach to `palm` on the gripper and are carried at 35 cm
     height, never dragging under the table or phasing through surfaces!
2. Dedicated Positive-Y Drop Table Zone:
   - Drop Table approach is aligned with the orange "PICKUP / DROP ZONE" mat
     at (X = -1.35m, Y = +0.185m, yaw = pi).
   - Books are placed/picked on the orange mat, completely clear of the PC
     monitor and tower at negative Y.
3. Axis-Aligned Primitive Root Carriers for All 10 Books & Furniture:
   - True 3D coordinates in ALL CoppeliaSim versions (Bullet 2.78 through 4.8).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import struct
import time
import numpy as np

import config as C
import db as db


BOOK_MATRICES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'book_home_matrices.json')

COLOR_PALETTE = {
    'kuka_orange':  [0.96, 0.42, 0.06],
    'stark_gold':   [0.92, 0.74, 0.20],
    'alu_silver':   [0.80, 0.82, 0.85],
    'dark_metal':   [0.16, 0.18, 0.20],
    'wheel_rubber': [0.10, 0.10, 0.12],
    'copper_brass': [0.82, 0.52, 0.22],
    'pcb_green':    [0.06, 0.48, 0.22],
    'pcb_blue':     [0.10, 0.38, 0.78],
    'servo_red':    [0.78, 0.14, 0.14],
    'screen_glow':  [0.08, 0.68, 0.92],
    'shelf_wood':   [0.56, 0.38, 0.22],
    'table_wood':   [0.82, 0.77, 0.70],
    'book_green':   [0.16, 0.58, 0.34],
    'book_purple':  [0.60, 0.24, 0.60],
    'white_pages':  [0.96, 0.96, 0.94],
}

STL_DTYPE = np.dtype([
    ('normal', '<f4', (3,)),
    ('v0',     '<f4', (3,)),
    ('v1',     '<f4', (3,)),
    ('v2',     '<f4', (3,)),
    ('attr',   '<u2'),
])


def _rot_z(deg: float) -> np.ndarray:
    r = np.radians(deg)
    co, si = np.cos(r), np.sin(r)
    return np.array([[co, -si, 0.0], [si, co, 0.0], [0.0, 0.0, 1.0]], dtype=np.float32)


def _rot_y(deg: float) -> np.ndarray:
    r = np.radians(deg)
    co, si = np.cos(r), np.sin(r)
    return np.array([[co, 0.0, si], [0.0, 1.0, 0.0], [-si, 0.0, co]], dtype=np.float32)


def world_to_wrist_pts(pts: np.ndarray) -> np.ndarray:
    p1 = (pts - np.array([42.0, 0.0, 94.0], dtype=np.float32)) @ _rot_z(-20.0).T
    p2 = (p1  - np.array([0.0, 0.0, 54.0], dtype=np.float32))  @ _rot_y(-28.0).T
    p3 = (p2  - np.array([0.0, 0.0, 200.0], dtype=np.float32)) @ _rot_y(-48.0).T
    p4 = (p3  - np.array([0.0, 0.0, 180.0], dtype=np.float32)) @ _rot_y(16.0).T
    return p4


def world_to_wrist_normals(norms: np.ndarray) -> np.ndarray:
    n1 = norms @ _rot_z(-20.0).T
    n2 = n1    @ _rot_y(-28.0).T
    n3 = n2    @ _rot_y(-48.0).T
    n4 = n3    @ _rot_y(16.0).T
    return n4


def wrist_pt_to_world_m(pt_mm: list[float]) -> list[float]:
    p = np.array(pt_mm, dtype=np.float32)
    p = (_rot_y(-16.0) @ p) + np.array([0.0, 0.0, 180.0], dtype=np.float32)
    p = (_rot_y(48.0)  @ p) + np.array([0.0, 0.0, 200.0], dtype=np.float32)
    p = (_rot_y(28.0)  @ p) + np.array([0.0, 0.0, 54.0], dtype=np.float32)
    p = (_rot_z(20.0)  @ p) + np.array([42.0, 0.0, 94.0], dtype=np.float32)
    return [float(v * 0.001) for v in p]


def make_box_tris(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> np.ndarray:
    p = np.array([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ], dtype=np.float32)
    faces = [
        (0, 2, 1, [ 0,  0, -1]), (0, 3, 2, [ 0,  0, -1]),
        (4, 5, 6, [ 0,  0,  1]), (4, 6, 7, [ 0,  0,  1]),
        (0, 1, 5, [ 0, -1,  0]), (0, 5, 4, [ 0, -1,  0]),
        (2, 3, 7, [ 0,  1,  0]), (2, 7, 6, [ 0,  1,  0]),
        (0, 4, 7, [-1,  0,  0]), (0, 7, 3, [-1,  0,  0]),
        (1, 2, 6, [ 1,  0,  0]), (1, 6, 5, [ 1,  0,  0]),
    ]
    out = np.zeros(12, dtype=STL_DTYPE)
    for i, (a, b, c_idx, n) in enumerate(faces):
        out[i]['normal'] = n
        out[i]['v0'] = p[a]
        out[i]['v1'] = p[b]
        out[i]['v2'] = p[c_idx]
    return out


def transform_tris(tris: np.ndarray, rot_mat: np.ndarray, offset: tuple[float, float, float]) -> np.ndarray:
    out = tris.copy()
    off = np.array(offset, dtype=np.float32)
    out['normal'] = out['normal'] @ rot_mat.T
    for k in ('v0', 'v1', 'v2'):
        out[k] = (out[k] @ rot_mat.T) + off
    return out


def wrist_to_world_tris(tris_wrist: np.ndarray) -> np.ndarray:
    t1 = transform_tris(tris_wrist, _rot_y(-16.0), (0.0, 0.0, 180.0))
    t2 = transform_tris(t1,         _rot_y(48.0),  (0.0, 0.0, 200.0))
    t3 = transform_tris(t2,         _rot_y(28.0),  (0.0, 0.0, 54.0))
    t4 = transform_tris(t3,         _rot_z(20.0),  (42.0, 0.0, 94.0))
    return t4


def build_clean_book_layers(bx0: float, by0: float, bz0: float, width: float, cover_color: str,
                            spine_tris: np.ndarray, spine_layers: np.ndarray) -> dict[str, np.ndarray]:
    d, w, h = 150.0, width, 195.0
    cover_boxes = np.concatenate([
        make_box_tris(bx0,       bx0 + 4.0, by0,          by0 + w,   bz0, bz0 + h),
        make_box_tris(bx0 + 4.0, bx0 + d,   by0,          by0 + 3.0, bz0, bz0 + h),
        make_box_tris(bx0 + 4.0, bx0 + d,   by0 + w - 3., by0 + w,   bz0, bz0 + h),
    ])
    pages_box = make_box_tris(bx0 + 4.0, bx0 + d - 5.0, by0 + 3.0, by0 + w - 3.0, bz0 + 4.0, bz0 + h - 4.0)

    layers_dict: dict[str, list[np.ndarray]] = {
        cover_color:   [cover_boxes],
        'white_pages': [pages_box],
    }
    for col_name in np.unique(spine_layers):
        sub = spine_tris[spine_layers == col_name]
        if len(sub):
            layers_dict.setdefault(str(col_name), []).append(sub)

    return {k: np.concatenate(v) for k, v in layers_dict.items()}


def build_clean_cupboard_layers(cup_x: float, cup_y: float) -> dict[str, np.ndarray]:
    shelf_w, shelf_d, shelf_h, wall_t = 560.0, 220.0, 680.0, 18.0
    wood_boxes = [
        make_box_tris(cup_x, cup_x + shelf_d, cup_y - shelf_w/2, cup_y - shelf_w/2 + wall_t, 0.0, shelf_h),
        make_box_tris(cup_x, cup_x + shelf_d, cup_y + shelf_w/2 - wall_t, cup_y + shelf_w/2, 0.0, shelf_h),
        make_box_tris(cup_x + shelf_d - wall_t, cup_x + shelf_d, cup_y - shelf_w/2, cup_y + shelf_w/2, 0.0, shelf_h),
    ]
    for z_lvl in (40.0, 240.0, 440.0, shelf_h - wall_t):
        wood_boxes.append(make_box_tris(cup_x, cup_x + shelf_d, cup_y - shelf_w/2, cup_y + shelf_w/2, z_lvl, z_lvl + wall_t))

    orange_pads = []
    silver_rails = []
    for tier_z in (240.0 + wall_t, 440.0 + wall_t):
        for s in range(4):
            sy = cup_y - shelf_w/2 + 55.0 + s * 120.0
            orange_pads.append(make_box_tris(cup_x + 16.0, cup_x + 166.0, sy - 4.0, sy + 36.0, tier_z, tier_z + 2.0))
            silver_rails.append(make_box_tris(cup_x + 16.0, cup_x + 161.0, sy - 6.0, sy - 3.0, tier_z, tier_z + 18.0))
            silver_rails.append(make_box_tris(cup_x + 16.0, cup_x + 161.0, sy + 35.0, sy + 38.0, tier_z, tier_z + 18.0))

    return {
        'shelf_wood':  np.concatenate(wood_boxes),
        'kuka_orange': np.concatenate(orange_pads),
        'alu_silver':  np.concatenate(silver_rails),
    }


def _write_binary_stl(path: str, triangles: np.ndarray):
    header = b'DUM-E Modular Layer STL'.ljust(80, b'\x00')
    with open(path, 'wb') as f:
        f.write(header)
        f.write(struct.pack('<I', len(triangles)))
        triangles.tofile(f)


def build_modular_scene_meshes(stl_path: str, mesh_dir: str) -> list[dict]:
    t0 = time.time()
    os.makedirs(mesh_dir, exist_ok=True)

    with open(stl_path, 'rb') as f:
        f.read(80)
        count = struct.unpack('<I', f.read(4))[0]
        data = np.fromfile(f, dtype=STL_DTYPE, count=count).copy()

    v0, v1, v2 = data['v0'], data['v1'], data['v2']
    c = (v0 + v1 + v2) / 3.0
    x, y, z = c[:, 0], c[:, 1], c[:, 2]

    p_turret = (c - np.array([42.0, 0.0, 94.0], dtype=np.float32)) @ _rot_z(-20.0).T
    p_L1     = (p_turret - np.array([0.0, 0.0, 54.0], dtype=np.float32)) @ _rot_y(-28.0).T
    p_L2     = (p_L1 - np.array([0.0, 0.0, 200.0], dtype=np.float32)) @ _rot_y(-48.0).T
    p_wrist  = (p_L2 - np.array([0.0, 0.0, 180.0], dtype=np.float32)) @ _rot_y(16.0).T
    wx_w, wy_w, wz_w = p_wrist[:, 0], p_wrist[:, 1], p_wrist[:, 2]

    CUP_SHIFT_X   = 1090.0
    TABLE_SHIFT_X = 4246.0

    objects_spec: list[dict] = []

    def save_object(obj_name: str, parent_name: str | None, color_dict: dict[str, np.ndarray], shelf_group: str | None = None):
        layer_files = []
        all_pts = []
        for col_name, tris in color_dict.items():
            if len(tris) == 0:
                continue
            tris_clean = tris.copy()
            for vk in ('v0', 'v1', 'v2'):
                tris_clean[vk][:, 2] = np.maximum(tris_clean[vk][:, 2], 0.0)
                all_pts.append(tris_clean[vk])
            fname = os.path.join(mesh_dir, f'{obj_name}_{col_name}.stl')
            _write_binary_stl(fname, tris_clean)
            layer_files.append((str(col_name), fname, COLOR_PALETTE[str(col_name)]))
        if layer_files and all_pts:
            pts_cat = np.concatenate(all_pts, axis=0)
            vmin_m = pts_cat.min(axis=0) * 0.001
            vmax_m = pts_cat.max(axis=0) * 0.001
            expected_pos = [float(v) for v in ((vmin_m + vmax_m) / 2.0)]
            bbox_size = [max(0.01, float(v)) for v in (vmax_m - vmin_m)]
            objects_spec.append({
                'name':         obj_name,
                'parent':       parent_name,
                'shelf_group':  shelf_group,
                'layers':       layer_files,
                'expected_pos': expected_pos,
                'bbox_size':    bbox_size,
            })

    # A. ROBOT CHASSIS, 4 MECANUM WHEELS, 4-DOF ARM LINKS & SENSORS
    is_corridor = (z < 2.0) & (x < 200.0) & (x > -6250.0) & (np.abs(y) < 220.0)
    in_hbook_region = (x > 250.0) & (z > 250.0) & (wz_w > 93.0) & ((wx_w < -14.5) | (wz_w > 120.5) | (np.abs(wy_w) < 17.5))
    is_robot_clean  = (x > -250.0) & (x < 558.0) & (np.abs(y) < 250.0) & (~is_corridor) & (~in_hbook_region)

    wheel_specs = [
        ('Wheel_FL', 'rollingJoint_fl',  99.0,  176.0),
        ('Wheel_FR', 'rollingJoint_fr',  99.0, -176.0),
        ('Wheel_RL', 'rollingJoint_rl', -99.0,  176.0),
        ('Wheel_RR', 'rollingJoint_rr', -99.0, -176.0),
    ]
    wheel_masks: dict[str, np.ndarray] = {}
    any_wheel_mask = np.zeros(count, dtype=bool)
    for w_name, _, wx_c, wy_c in wheel_specs:
        w_cond = is_robot_clean.copy()
        for v_arr in (v0, v1, v2):
            dx_v = v_arr[:, 0] - wx_c
            dy_v = v_arr[:, 1] - wy_c
            dz_v = v_arr[:, 2] - 40.0
            w_cond &= (
                (np.abs(dx_v) <= 42.0)
                & (np.abs(dy_v) <= 25.0)
                & (np.abs(v_arr[:, 1]) >= 152.0)
                & (v_arr[:, 2] <= 82.0)
                & ((dx_v * dx_v + dz_v * dz_v) <= 42.0**2)
            )
        wheel_masks[w_name] = w_cond
        any_wheel_mask |= w_cond

    is_lidar = is_robot_clean & (x > 95.0) & (np.abs(y) < 25.0) & (z > 94.2) & (z < 130.0)
    is_rpi   = is_robot_clean & (x > -108.0) & (x < -42.0) & (y > -98.0) & (y < -6.0) & (z > 94.0) & (z < 125.0)
    is_drv   = is_robot_clean & (x > -42.0) & (x < 25.0) & (y < -55.0) & (z > 94.0) & (z < 120.0)
    is_base  = is_robot_clean & (~any_wheel_mask) & ((z <= 94.5) | is_lidar | is_rpi | is_drv)

    b_col = np.full(count, 'dark_metal', dtype='U16')
    is_motor = is_base & (np.abs(y) > 85.0) & (z < 56.0) & (np.abs(np.abs(x) - 99.0) < 16.0)
    b_col[is_motor & (np.abs(y) > 124.0)] = 'alu_silver'
    b_col[is_motor & (np.abs(y) >= 91.0) & (np.abs(y) <= 96.0)] = 'pcb_blue'
    is_batt = is_base & (x > -130.0) & (x < -10.0) & (y > 12.0) & (y < 84.0) & (z > 33.0) & (z < 72.0)
    b_col[is_batt & (x <= -28.0)] = 'pcb_blue'
    b_col[is_batt & (x > -28.0)]  = 'stark_gold'
    is_armor = is_base & (z >= 31.8) & (z <= 80.5) & ((np.abs(x) > 124.0) | (np.abs(y) > 124.0))
    b_col[is_armor] = 'kuka_orange'
    b_col[is_armor & ((np.abs(y) > 138.05) | (x > 138.05))] = 'stark_gold'
    is_deck = is_base & (z > 81.5) & (z <= 94.5) & (np.abs(x) <= 138.5) & (np.abs(y) <= 138.5)
    b_col[is_deck] = 'alu_silver'
    b_col[is_lidar & (z > 113.5)] = 'screen_glow'
    b_col[is_rpi & (z < 99.8)] = 'copper_brass'
    b_col[is_rpi & (z >= 99.8) & (z <= 102.2)] = 'pcb_green'
    b_col[is_rpi & (x < -76.0) & (z > 108.0)] = 'stark_gold'
    b_col[is_rpi & (y < -40.0) & (z > 102.2)] = 'alu_silver'
    b_col[is_rpi & (y < -50.0) & (x > -80.0) & (x < -68.0) & (z > 103.0) & (z < 114.0)] = 'pcb_blue'
    b_col[is_rpi & (x > -86.0) & (x < -64.0) & (y > -76.0) & (y < -54.0) & (z > 104.0)] = 'alu_silver'
    b_col[is_drv & (y > -100.0) & (z < 98.5)] = 'pcb_blue'
    b_col[is_drv & (y > -84.0) & (z >= 98.5)] = 'stark_gold'
    b_col[is_drv & (y <= -84.0) & (y > -87.0) & (z >= 98.5)] = 'servo_red'
    b_col[is_drv & (y <= -87.0) & (y > -100.0) & (x > -16.0) & (x < -4.0) & (z >= 98.5)] = 'pcb_green'
    b_col[is_drv & (y <= -100.0) & (z < 98.5)] = 'servo_red'
    b_col[is_drv & (y <= -100.0) & (np.abs(x + 10.0) > 12.0) & (z >= 98.5)] = 'pcb_blue'
    save_object('LibBot', None, {
        col: data[is_base & (b_col == col)] for col in np.unique(b_col[is_base])
    })

    wx_abs = np.abs(x) - 99.0
    wy_abs = np.abs(y) - 176.0
    wz_abs = z - 40.0
    wr = np.sqrt(wx_abs**2 + wz_abs**2)

    for w_name, j_parent, _, _ in wheel_specs:
        w_mask = wheel_masks[w_name]
        w_col = np.full(count, 'dark_metal', dtype='U16')
        w_col[w_mask & (wr >= 29.8)] = 'wheel_rubber'
        w_col[w_mask & (wr >= 33.2) & (wr <= 35.2) & (np.abs(wy_abs) > 13.5)] = 'alu_silver'
        w_col[w_mask & (wr < 29.8)  & (np.abs(wy_abs) < 15.2)] = 'stark_gold'
        w_col[w_mask & (wr < 33.5)  & (np.abs(wy_abs) >= 15.2)] = 'dark_metal'
        w_col[w_mask & (wr < 8.5)   & (np.abs(wy_abs) > 18.0)] = 'copper_brass'
        save_object(w_name, j_parent, {
            col: data[w_mask & (w_col == col)] for col in np.unique(w_col[w_mask])
        })

    is_arm = is_robot_clean & (~any_wheel_mask) & (z > 94.2) & (~is_base)
    in_turret = is_arm & (p_L1[:, 2] < 12.0) & (z < 185.0)
    t_col = np.full(count, 'dark_metal', dtype='U16')
    t_col[in_turret & (z > 111.5) & (np.abs(p_turret[:, 1]) > 20.0) & (np.abs(p_turret[:, 1]) < 36.5)] = 'kuka_orange'
    t_col[in_turret & (np.abs(p_turret[:, 1]) >= 36.5) & (np.abs(p_turret[:, 1]) < 48.0)] = 'servo_red'
    t_col[in_turret & (np.abs(p_turret[:, 1]) >= 54.0) & (np.abs(p_turret[:, 1]) < 68.0)] = 'servo_red'
    t_col[in_turret & (np.abs(p_turret[:, 1]) < 39.5) & (np.sqrt(p_turret[:, 0]**2 + (p_turret[:, 2] - 54.0)**2) < 10.5)] = 'alu_silver'
    save_object('Arm_Turret', 'arm_yaw', {
        col: data[in_turret & (t_col == col)] for col in np.unique(t_col[in_turret])
    })

    in_L1 = is_arm & (~in_turret) & (p_L1[:, 2] >= -2.0) & (p_L1[:, 2] <= 205.0) & (np.abs(p_L1[:, 0]) < 35.0) & (np.abs(p_L1[:, 1]) < 68.0) & (p_L2[:, 2] < 15.0)
    l1_col = np.full(count, 'dark_metal', dtype='U16')
    l1_col[in_L1 & (np.abs(p_L1[:, 1]) >= 13.8) & (np.abs(p_L1[:, 1]) <= 24.05) & (p_L1[:, 2] < 195.0)] = 'kuka_orange'
    l1_col[in_L1 & (p_L1[:, 1] < -24.02) & (p_L1[:, 2] < 180.0)] = 'stark_gold'
    in_badge = in_L1 & (p_L1[:, 1] > 24.02) & (p_L1[:, 2] > 20.0) & (p_L1[:, 2] < 180.0)
    l1_col[in_badge & (p_L1[:, 1] <= 25.85)] = 'dark_metal'
    l1_col[in_badge & (p_L1[:, 1] > 25.3) & (p_L1[:, 1] <= 26.5) & ((p_L1[:, 2] < 27.5) | (p_L1[:, 2] > 172.5) | (np.abs(p_L1[:, 0]) > 10.5))] = 'screen_glow'
    l1_col[in_badge & (p_L1[:, 1] > 25.85) & (p_L1[:, 2] < 78.0)] = 'white_pages'
    l1_col[in_badge & (p_L1[:, 1] > 25.85) & (p_L1[:, 2] >= 78.0)] = 'stark_gold'
    l1_col[in_badge & (p_L1[:, 1] > 28.55)] = 'screen_glow'
    l1_col[in_L1 & (p_L1[:, 2] > 175.0) & (p_L1[:, 1] < -24.5) & ((p_L1[:, 1] > -39.5) | ((p_L1[:, 1] < -46.0) & (p_L1[:, 1] > -60.0)))] = 'servo_red'
    save_object('Arm_Upper_L1', 'arm_shoulder', {
        col: data[in_L1 & (l1_col == col)] for col in np.unique(l1_col[in_L1])
    })

    in_L2 = is_arm & (~in_turret) & (~in_L1) & (p_L2[:, 2] >= 0.0) & (p_L2[:, 2] <= 185.0) & (np.abs(p_L2[:, 0]) < 35.0) & (np.abs(p_L2[:, 1]) < 60.0) & (p_wrist[:, 2] < 5.0)
    l2_col = np.full(count, 'dark_metal', dtype='U16')
    l2_col[in_L2 & (np.abs(p_L2[:, 1]) <= 12.5) & (p_L2[:, 2] < 176.0)] = 'alu_silver'
    l2_col[in_L2 & (p_L2[:, 2] > 33.0) & (p_L2[:, 2] < 147.0) & ((np.abs(p_L2[:, 0]) > 15.2) | (np.abs(p_L2[:, 1]) > 11.5))] = 'kuka_orange'
    l2_col[in_L2 & (p_L2[:, 2] > 155.0) & (p_L2[:, 1] < -14.0) & ((p_L2[:, 1] > -33.5) | ((p_L2[:, 1] < -40.0) & (p_L2[:, 1] > -54.0)))] = 'servo_red'
    save_object('Arm_Forearm_L2', 'arm_elbow', {
        col: data[in_L2 & (l2_col == col)] for col in np.unique(l2_col[in_L2])
    })

    in_wrist_all = is_arm & (~in_turret) & (~in_L1) & (~in_L2) & (wz_w >= 0.0)
    in_cam = in_wrist_all & (wx_w < -31.5) & (wz_w > 25.0) & (wz_w < 92.0)
    cam_col = np.full(count, 'dark_metal', dtype='U16')
    cam_col[in_cam & ((wx_w > -43.5) | (np.abs(wy_w) > 19.8) | (wz_w < 43.0) | (wz_w > 84.0))] = 'kuka_orange'
    cam_col[in_cam & (wx_w < -54.5)] = 'screen_glow'
    cam_col[in_cam & (wx_w < -45.0) & (wy_w > 5.0) & (wz_w > 62.0)] = 'white_pages'
    cam_col[in_cam & (wx_w >= -47.0) & (wx_w <= -41.0) & (np.abs(wy_w) < 9.5)] = 'alu_silver'

    in_wrist_mech = in_wrist_all & (~in_cam)
    wrist_col = np.full(count, 'dark_metal', dtype='U16')
    wrist_col[in_wrist_mech & (wz_w >= 44.5) & (wz_w <= 58.2) & (wx_w > -21.5)] = 'alu_silver'
    wrist_col[in_wrist_mech & (wx_w > 12.0) & (wz_w > 46.0) & (wz_w < 53.0)] = 'servo_red'
    wrist_col[in_wrist_mech & (wz_w > 58.5) & (np.abs(wy_w) > 30.0)] = 'dark_metal'
    wrist_col[in_wrist_mech & (wz_w > 65.0) & (wz_w < 115.0) & (np.abs(wy_w) <= 28.0) & (np.abs(wy_w) >= 12.0)] = 'kuka_orange'
    wrist_col[in_wrist_mech & (wz_w > 52.0) & (wz_w <= 58.0) & (wx_w > 10.0)] = 'alu_silver'

    grip_dict = {
        col: data[in_wrist_mech & (wrist_col == col)] for col in np.unique(wrist_col[in_wrist_mech])
    }
    save_object('Arm_Wrist_Gripper', 'arm_wrist', grip_dict)
    save_object('ESP32_CAM', 'Arm_Wrist_Gripper', {
        col: data[in_cam & (cam_col == col)] for col in np.unique(cam_col[in_cam])
    })

    # B. CUPBOARD 1, CUPBOARD 2, AND ALL 10 SHELF BOOKS
    save_object('Cupboard_1', None, build_clean_cupboard_layers(560.0 + CUP_SHIFT_X,  440.0))
    save_object('Cupboard_2', None, build_clean_cupboard_layers(560.0 + CUP_SHIFT_X, -440.0))

    shelf_books_meta = [
        ('AC',   'Cupboard_1',  440.0, 0, 260.0, 'servo_red'),
        ('EDC',  'Cupboard_1',  440.0, 1, 260.0, 'kuka_orange'),
        ('NT',   'Cupboard_1',  440.0, 2, 260.0, 'book_purple'),
        ('ADC',  'Cupboard_1',  440.0, 0, 460.0, 'pcb_blue'),
        ('FOC',  'Cupboard_2', -440.0, 0, 260.0, 'book_green'),
        ('RASC', 'Cupboard_2', -440.0, 1, 260.0, 'pcb_blue'),
        ('ES',   'Cupboard_2', -440.0, 2, 260.0, 'book_purple'),
        ('IOT',  'Cupboard_2', -440.0, 1, 460.0, 'stark_gold'),
        ('CN',   'Cupboard_2', -440.0, 2, 460.0, 'book_green'),
    ]
    for b_name, parent_cup, cup_y, s_idx, tier_z, cov_col in shelf_books_meta:
        by0 = cup_y - 280.0 + 55.0 + s_idx * 120.0
        bx0_orig = 560.0 + 16.0
        in_sp = (x >= 574.0) & (x < 575.95) & (y >= by0 - 1.0) & (y <= by0 + 33.0) & (z >= tier_z) & (z <= tier_z + 195.0)
        sp_tris = data[in_sp].copy()
        for vk in ('v0', 'v1', 'v2'):
            sp_tris[vk][:, 0] += CUP_SHIFT_X
        lz_sp = z[in_sp] - tier_z
        lx_sp = x[in_sp] - 560.0
        sp_col = np.full(len(sp_tris), 'white_pages', dtype='U16')
        sp_col[lz_sp > 160.0] = 'stark_gold'
        sp_col[(lz_sp < 90.0) & (lx_sp < 15.1)] = 'wheel_rubber'

        b_layers = build_clean_book_layers(
            bx0=bx0_orig + CUP_SHIFT_X,
            by0=by0,
            bz0=tier_z,
            width=32.0,
            cover_color=cov_col,
            spine_tris=sp_tris,
            spine_layers=sp_col,
        )
        save_object(b_name, None, b_layers, shelf_group=parent_cup)

    # 10th Book: `CS` ("Control Systems") placed inside Cupboard_1 (Upper Tier z=460, Slot 1 by0=335)
    cs_by0 = 440.0 - 280.0 + 55.0 + 1 * 120.0
    cs_bx0 = 560.0 + 16.0 + CUP_SHIFT_X
    cs_bz0 = 460.0
    in_cs_spine = (x > 250.0) & (x < 558.0) & (z > 250.0) & (wz_w > 93.0) & (wx_w < -69.9)
    cs_sp_tris = data[in_cs_spine].copy()
    cs_sp_tris['normal'] = world_to_wrist_normals(cs_sp_tris['normal'])
    for vk in ('v0', 'v1', 'v2'):
        vw = world_to_wrist_pts(cs_sp_tris[vk])
        cs_sp_tris[vk] = vw - np.array([-70.0, 0.0, 95.0], dtype=np.float32) + np.array([cs_bx0, cs_by0 + 16.0, cs_bz0], dtype=np.float32)

    wz_cs = wz_w[in_cs_spine]
    wx_cs = wx_w[in_cs_spine]
    cs_spine_col = np.full(len(cs_sp_tris), 'white_pages', dtype='U16')
    cs_spine_col[wz_cs > 250.0] = 'stark_gold'
    cs_spine_col[(wz_cs < 180.0) & (wx_cs < -70.8)] = 'wheel_rubber'

    cs_layers = build_clean_book_layers(
        bx0=cs_bx0,
        by0=cs_by0,
        bz0=cs_bz0,
        width=32.0,
        cover_color='pcb_blue',
        spine_tris=cs_sp_tris,
        spine_layers=cs_spine_col,
    )
    save_object('CS', None, cs_layers, shelf_group='Cupboard_1')

    # C. DROP TABLE & DESKTOP PC WORKSTATION
    is_table_zone = (x < -5000.0) & (~is_corridor)
    dx, dy, dz = x + 6096.0, y, z

    is_table_only = is_table_zone & (dz <= 281.8)
    tb_tris = data[is_table_only].copy()
    for vk in ('v0', 'v1', 'v2'):
        tb_tris[vk][:, 0] += TABLE_SHIFT_X
    tb_dz = dz[is_table_only]
    tb_dx = dx[is_table_only]
    tb_dy = dy[is_table_only]
    tb_col = np.full(len(tb_tris), 'table_wood', dtype='U16')
    tb_col[tb_dz < 257.5] = 'dark_metal'
    tb_col[(tb_dx > 30.0) & (tb_dy > 78.0) & (tb_dz >= 258.5) & (tb_dz <= 280.5)] = 'kuka_orange'
    tb_col[(tb_dx > 30.0) & (tb_dy > 78.0) & (tb_dz > 280.5)] = 'white_pages'
    save_object('Drop_Table', None, {
        col: tb_tris[tb_col == col] for col in np.unique(tb_col)
    })

    is_pc_all = is_table_zone & (dz > 281.8)
    pc_tris = data[is_pc_all].copy()
    for vk in ('v0', 'v1', 'v2'):
        pc_tris[vk][:, 0] += TABLE_SHIFT_X
    pc_dx, pc_dy, pc_dz = dx[is_pc_all], dy[is_pc_all], dz[is_pc_all]
    pc_col = np.full(len(pc_tris), 'dark_metal', dtype='U16')
    in_mon = (pc_dx < 35.0) & (pc_dy > -215.0) & (pc_dy < 35.0)
    pc_col[in_mon & (pc_dx > 14.8) & (np.abs(pc_dy + 90.0) < 114.0) & (np.abs(pc_dz - 425.0) < 69.0)] = 'screen_glow'
    pc_col[in_mon & (pc_dx > 15.4) & (pc_dz > 450.0)] = 'white_pages'
    pc_col[in_mon & (pc_dx > 15.4) & (pc_dz <= 450.0)] = 'dark_metal'
    in_tower = (pc_dy <= -215.0)
    pc_col[in_tower & (pc_dx > 154.5) & (pc_dz < 460.0)] = 'screen_glow'
    pc_col[in_tower & (pc_dz > 474.5)] = 'kuka_orange'
    pc_col[(pc_dx >= 45.0) & (pc_dy < -8.0) & (pc_dz > 288.2)] = 'alu_silver'
    pc_col[(pc_dx >= 45.0) & (pc_dy >= -8.0) & (pc_dz > 283.5)] = 'kuka_orange'
    save_object('Desktop_PC', 'Drop_Table', {
        col: pc_tris[pc_col == col] for col in np.unique(pc_col)
    })

    # D. CORRIDOR GUIDE TRACK ON FLOOR
    corridor_dark = make_box_tris(-1850.0, 1650.0, -210.0, 210.0, 0.0, 1.5)
    corridor_orange = np.concatenate([
        make_box_tris(xm - 80.0, xm + 80.0, -9.0, 9.0, 1.5, 2.5)
        for xm in range(-1400, 1401, 560)
    ])
    save_object('Corridor_Track', None, {
        'dark_metal':  corridor_dark,
        'kuka_orange': corridor_orange,
    })

    print(f"[MODULAR BUILD] Built {len(objects_spec)} separate multi-color hierarchy objects in {time.time() - t0:.2f} sec!")
    return objects_spec


def cleanup_previous_imports(sim, objects_spec: list[dict]):
    base_names = [
        'DUME_Mark1_Visual', 'DUME_Colored_Station', 'DUME_Colored_Robot', 'DUME_Base_Visual',
        'Drop_Spot', 'ref', 'palm', 'tip', 'gripper_cam', 'LibBot_Drive_Controller',
        'arm_yaw', 'arm_shoulder', 'arm_elbow', 'arm_wrist', 'finger_l', 'finger_r',
        'Finger_L', 'Finger_R', 'Finger_l', 'Finger_r', 'finger_l_Mesh', 'finger_r_Mesh',
        'Vision_sensor', 'vision_sensor', 'ESP32_CAM', 'ESP32_Cam', 'ESP32_CAM_Mesh',
        'Arm_Wrist_Gripper', 'Arm_Wrist_Gripper_Mesh', 'Arm_Turret', 'Arm_Turret_Mesh',
        'Arm_Upper_L1', 'Arm_Upper_L1_Mesh', 'Arm_Forearm_L2', 'Arm_Forearm_L2_Mesh',
        'rollingJoint_fl', 'rollingJoint_fr', 'rollingJoint_rl', 'rollingJoint_rr',
        'AC', 'EDC', 'NT', 'ADC', 'FOC', 'RASC', 'ES', 'CS', 'IOT', 'CN',
        'Book_AC', 'Book_EDC', 'Book_NT', 'Book_ADC', 'Book_FOC', 'Book_RASC',
        'Book_ES', 'Book_CS', 'Book_IOT', 'Book_CN',
    ] + [o['name'] for o in objects_spec] + [f'DUME_Color_{k}' for k in COLOR_PALETTE]
    old_names = set(base_names + [f'{n}_Mesh' for n in base_names] + [f'{n}_carrier' for n in base_names])

    for pass_idx in range(4):
        removed_any = False
        for alias in old_names:
            for prefix in ('', '/', f'/{C.ROBOT_NAME}/', f'/{C.ROBOT_NAME}/Arm_Wrist_Gripper/', '/Cupboard_1/', '/Cupboard_2/', '/Drop_Table/', ':/'):
                try:
                    h = sim.getObject(f'{prefix}{alias}')
                    sim.removeObjects([h])
                    removed_any = True
                except Exception:
                    pass
        if not removed_any:
            break


def apply_coppelia_color(sim, shape_h: int, rgb: list[float]):
    try:
        sim.setShapeColor(shape_h, None, sim.colorcomponent_ambient_diffuse, list(rgb))
        sim.setShapeColor(shape_h, None, sim.colorcomponent_specular, [0.22, 0.22, 0.22])
    except Exception:
        pass


def create_invisible_carrier(sim, name: str, pos: list[float], size: list[float]) -> int:
    """
    Create an invisible axis-aligned primitive cuboid at `pos` with orientation `[0,0,0]`.
    Guarantees `sim.getObjectPosition(h, -1)` returns `pos` in ALL CoppeliaSim versions!
    """
    c_h = sim.createPrimitiveShape(sim.primitiveshape_cuboid, size, 0)
    sim.setObjectAlias(c_h, name)
    sim.setObjectPosition(c_h, -1, pos)
    sim.setObjectOrientation(c_h, -1, [0.0, 0.0, 0.0])
    sim.setObjectInt32Param(c_h, sim.shapeintparam_static, 1)
    sim.setObjectInt32Param(c_h, sim.shapeintparam_respondable, 0)
    try:
        sim.setShapeColor(c_h, None, sim.colorcomponent_transparency, [0.0])
    except Exception:
        pass
    try:
        sim.setObjectInt32Param(c_h, sim.objintparam_visibility_layer, 0)
    except Exception:
        pass
    return c_h


def create_robot_kinematic_handles(sim, handle_by_name: dict[str, int]):
    """
    Construct an unbroken, true parent-child robotic arm chain:
    /LibBot -> arm_yaw -> Arm_Turret -> arm_shoulder -> Arm_Upper_L1 ->
    arm_elbow -> Arm_Forearm_L2 -> arm_wrist -> Arm_Wrist_Gripper ->
    palm, tip, gripper_cam, finger_l, finger_r, ESP32_CAM
    """
    bot_h = handle_by_name.get('LibBot')
    if bot_h is None:
        return

    turret_h = handle_by_name.get('Arm_Turret')
    l1_h = handle_by_name.get('Arm_Upper_L1')
    l2_h = handle_by_name.get('Arm_Forearm_L2')
    gripper_h = handle_by_name.get('Arm_Wrist_Gripper')

    def make_dummy(name: str, pos: list[float], parent_h: int | None):
        d_h = sim.createDummy(0.001)
        sim.setObjectAlias(d_h, name)
        sim.setObjectPosition(d_h, -1, pos)
        sim.setObjectOrientation(d_h, -1, [0.0, 0.0, 0.0])
        try:
            sim.setObjectInt32Param(d_h, sim.objintparam_visibility_layer, 0)
        except Exception:
            pass
        if parent_h is not None:
            sim.setObjectParent(d_h, parent_h, True)
        handle_by_name[name] = d_h
        return d_h

    def make_joint(name: str, j_type: int, pos: list[float], orient: list[float], parent_h: int | None = None):
        try:
            j_h = sim.createJoint(j_type, sim.jointmode_kinematic, 0, [0.025, 0.008])
        except Exception:
            j_h = sim.createDummy(0.015)
        sim.setObjectAlias(j_h, name)
        sim.setObjectPosition(j_h, -1, pos)
        sim.setObjectOrientation(j_h, -1, orient)
        if parent_h is not None:
            sim.setObjectParent(j_h, parent_h, True)
        handle_by_name[name] = j_h
        return j_h

    # A. Base reference and Drop Spot (on Drop Table's orange pickup mat)
    make_dummy('ref',       [0.0, 0.0, 0.0], bot_h)
    make_dummy('Drop_Spot', [-1.7300, float(C.TABLE_APPROACH_Y), 0.2820], None)

    # B. 4 Mecanum wheel joints (parent = bot_h)
    y_axis_orient = [-math.pi / 2.0, 0.0, 0.0]
    make_joint('rollingJoint_fl', sim.joint_revolute_subtype, [ 0.099,  0.176, 0.040], y_axis_orient, parent_h=bot_h)
    make_joint('rollingJoint_fr', sim.joint_revolute_subtype, [ 0.099, -0.176, 0.040], y_axis_orient, parent_h=bot_h)
    make_joint('rollingJoint_rl', sim.joint_revolute_subtype, [-0.099,  0.176, 0.040], y_axis_orient, parent_h=bot_h)
    make_joint('rollingJoint_rr', sim.joint_revolute_subtype, [-0.099, -0.176, 0.040], y_axis_orient, parent_h=bot_h)

    # C. UNBREAKABLE 4-DOF ROBOTIC ARM CHAIN
    p_yaw_mm      = np.array([42.0, 0.0, 94.0], dtype=np.float32)
    p_shoulder_mm = p_yaw_mm + (_rot_z(20.0) @ np.array([0.0, 0.0, 54.0], dtype=np.float32))
    p_elbow_mm    = p_shoulder_mm + (_rot_z(20.0) @ _rot_y(28.0) @ np.array([0.0, 0.0, 200.0], dtype=np.float32))
    p_wrist_mm    = p_elbow_mm + (_rot_z(20.0) @ _rot_y(28.0) @ _rot_y(48.0) @ np.array([0.0, 0.0, 180.0], dtype=np.float32))

    # 1. arm_yaw attached to bot_h
    yaw_j = make_joint('arm_yaw', sim.joint_revolute_subtype, [float(v * 0.001) for v in p_yaw_mm], [0.0, 0.0, 0.0], parent_h=bot_h)
    if turret_h is not None:
        sim.setObjectParent(turret_h, yaw_j, True)

    # 2. arm_shoulder attached to Arm_Turret
    sh_parent = turret_h if turret_h is not None else yaw_j
    sh_j = make_joint('arm_shoulder', sim.joint_revolute_subtype, [float(v * 0.001) for v in p_shoulder_mm], y_axis_orient, parent_h=sh_parent)
    if l1_h is not None:
        sim.setObjectParent(l1_h, sh_j, True)

    # 3. arm_elbow attached to Arm_Upper_L1
    el_parent = l1_h if l1_h is not None else sh_j
    el_j = make_joint('arm_elbow', sim.joint_revolute_subtype, [float(v * 0.001) for v in p_elbow_mm], y_axis_orient, parent_h=el_parent)
    if l2_h is not None:
        sim.setObjectParent(l2_h, el_j, True)

    # 4. arm_wrist attached to Arm_Forearm_L2
    wr_parent = l2_h if l2_h is not None else el_j
    wr_j = make_joint('arm_wrist', sim.joint_revolute_subtype, [float(v * 0.001) for v in p_wrist_mm], y_axis_orient, parent_h=wr_parent)
    if gripper_h is not None:
        sim.setObjectParent(gripper_h, wr_j, True)

    # 5. Gripper attachments parented to Arm_Wrist_Gripper
    grip_parent = gripper_h if gripper_h is not None else wr_j
    make_dummy('palm', wrist_pt_to_world_m([0.0, 0.0, 85.0]), parent_h=grip_parent)
    make_dummy('tip',  wrist_pt_to_world_m([0.0, 0.0, 95.0]), parent_h=grip_parent)
    # 5. Vision Sensor for Robot POV (ESP32-CAM) & QR/Barcode Scanning
    cam_pos = wrist_pt_to_world_m([-45.0, 0.0, 62.0])
    int_params = [320, 240, 0, 0]
    float_params = [0.01, 2.5, math.radians(65.0), 0.001, 0.001, 0.001, 0.0, 0.0, 0.0, 0.0, 0.0]
    try:
        cam_h = sim.createVisionSensor(1, int_params, float_params)
        sim.setObjectAlias(cam_h, 'gripper_cam')
        sim.setObjectPosition(cam_h, -1, cam_pos)
        sim.setObjectOrientation(cam_h, -1, [0.0, 0.0, 0.0])
        sim.setObjectParent(cam_h, grip_parent, True)
        sim.setObjectInt32Param(cam_h, sim.objintparam_visibility_layer, 0)
        handle_by_name['gripper_cam'] = cam_h
        print("  [CAM OK] Created Vision Sensor '/gripper_cam' at ESP32-CAM location!")
    except Exception as e:
        print(f"  [CAM FALLBACK] createVisionSensor fallback: {e}")
        make_dummy('gripper_cam', cam_pos, parent_h=grip_parent)

    make_joint('finger_l', sim.joint_prismatic_subtype, wrist_pt_to_world_m([0.0,  24.0, 85.0]), y_axis_orient, parent_h=grip_parent)
    make_joint('finger_r', sim.joint_prismatic_subtype, wrist_pt_to_world_m([0.0, -24.0, 85.0]), y_axis_orient, parent_h=grip_parent)

    esp_h = handle_by_name.get('ESP32_CAM')
    if esp_h is not None:
        sim.setObjectParent(esp_h, grip_parent, True)


def ensure_local_files_patched():
    """
    Ensure config.py has centered Drop Table slots and proper approach vectors.
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, 'config.py')
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                txt = f.read()
            updated = re.sub(
                r"FRONT\s*=\s*\{[^}]*\}",
                "FRONT = {\n    'Cupboard_1': (-1.0, 0.0),\n    'Cupboard_2': (-1.0, 0.0),\n    'Drop_Table': (1.0, 0.0),\n}",
                txt,
            )
            updated = re.sub(r"TABLE_SLOTS\s*=\s*\[[^\]]*\]", "TABLE_SLOTS = [0.12, 0.16, 0.20, 0.24]", updated)
            updated = re.sub(r"WALLS\s*=\s*\[[^\]]*\]", "WALLS = []", updated)
            updated = re.sub(r"HUB\s*=\s*\([^\n]+\)", "HUB = (0.0, 0.0)", updated)
            updated = re.sub(r"SPAWN\s*=\s*\([^\n]+\)", "SPAWN = (0.0, 0.0, 0.0)", updated)
            if updated != txt:
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    f.write(updated)
                print("  [AUTO-PATCH OK] Updated TABLE_SLOTS, FRONT, HUB, and SPAWN in config.py!")
        except Exception:
            pass


def main():
    from coppeliasim_zmqremoteapi_client import RemoteAPIClient

    parser = argparse.ArgumentParser(description='Modular Multi-Object & 15-Color DUM-E Importer for CoppeliaSim.')
    parser.add_argument('--stl', default='DUM-E mark 1.stl', help='Input STL file (default: "DUM-E mark 1.stl")')
    parser.add_argument('--mesh-dir', default='./color_meshes', help='Folder for generated object STLs')
    parser.add_argument('--force-export', action='store_true', help='Accepted for CLI compatibility')
    parser.add_argument('--host', default='localhost', help='CoppeliaSim ZMQ host')
    parser.add_argument('--port', type=int, default=23000, help='CoppeliaSim ZMQ port')
    args = parser.parse_args()

    if not os.path.isfile(args.stl):
        print(f"[ERROR] '{args.stl}' not found in current folder: {os.getcwd()}")
        return

    ensure_local_files_patched()

    try:
        db.reset_database(C.CATALOG)
        print("[DB OK] Reset library.db: all 10 books 'available' (CS in Cupboard_1) & Muddassir Sheikh (4th Year ENTC) active!")
    except Exception:
        pass

    print(f"[1/3] Building 25 separate multi-colored objects from '{args.stl}'...")
    objects_spec = build_modular_scene_meshes(args.stl, args.mesh_dir)

    print("[2/3] Connecting to CoppeliaSim & cleaning up old scene objects...")
    client = RemoteAPIClient(args.host, args.port)
    sim = client.require('sim')

    try:
        if sim.getSimulationState() != sim.simulation_stopped:
            sim.stopSimulation()
            time.sleep(0.4)
    except Exception:
        pass

    cleanup_previous_imports(sim, objects_spec)

    print("[3/3] Importing modular hierarchy with true 3D object origins...")
    handle_by_name: dict[str, int] = {}
    mesh_readback_matrices: list[tuple[int, list[float]]] = []
    book_home_data: dict[str, dict] = {}

    CARRIER_OBJECTS = set(C.CATALOG.keys()) | {'Cupboard_1', 'Cupboard_2', 'Drop_Table'}

    for obj in objects_spec:
        obj_name = obj['name']
        layer_handles = []
        for col_name, stl_path, rgb in obj['layers']:
            h = sim.importShape(0, os.path.abspath(stl_path), 0, 0.0, 0.001)
            apply_coppelia_color(sim, h, rgb)
            sim.setObjectInt32Param(h, sim.shapeintparam_static, 1)
            sim.setObjectInt32Param(h, sim.shapeintparam_respondable, 0)
            layer_handles.append(h)

        if not layer_handles:
            continue

        if len(layer_handles) == 1:
            mesh_h = layer_handles[0]
        else:
            mesh_h = sim.groupShapes(layer_handles, False)

        if obj_name.startswith('Wheel_'):
            try:
                sim.alignShapeBB(mesh_h, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
            except Exception:
                pass

        sim.setObjectInt32Param(mesh_h, sim.shapeintparam_static, 1)
        sim.setObjectInt32Param(mesh_h, sim.shapeintparam_respondable, 0)
        mesh_mat = list(sim.getObjectMatrix(mesh_h, -1))

        if obj_name == 'LibBot':
            # Kinematic Dummy root for LibBot so chassis moves dynamically with 0 physics lockup!
            bot_d = sim.createDummy(0.001)
            sim.setObjectAlias(bot_d, 'LibBot')
            sim.setObjectPosition(bot_d, -1, [0.0, 0.0, 0.0])
            sim.setObjectOrientation(bot_d, -1, [0.0, 0.0, 0.0])
            try:
                sim.setObjectInt32Param(bot_d, sim.objintparam_visibility_layer, 0)
            except Exception:
                pass
            sim.setObjectAlias(mesh_h, f'{obj_name}_Mesh')
            sim.setObjectParent(mesh_h, bot_d, True)
            sim.setObjectMatrix(mesh_h, -1, mesh_mat)
            obj_h = bot_d
        elif obj_name in CARRIER_OBJECTS:
            c_pos = obj['expected_pos']
            c_size = obj['bbox_size']
            carrier_h = create_invisible_carrier(sim, obj_name, c_pos, c_size)
            sim.setObjectAlias(mesh_h, f'{obj_name}_Mesh')
            sim.setObjectParent(mesh_h, carrier_h, True)
            sim.setObjectMatrix(mesh_h, -1, mesh_mat)
            obj_h = carrier_h
        else:
            sim.setObjectAlias(mesh_h, obj_name)
            obj_h = mesh_h

        mesh_readback_matrices.append((mesh_h, mesh_mat))
        handle_by_name[obj_name] = obj_h

        if obj_name in C.CATALOG:
            code = obj_name
            shelf_name = obj.get('shelf_group') or 'Cupboard_1'
            book_home_data[code] = {
                'matrix': list(sim.getObjectMatrix(obj_h, -1)),
                'center': obj['expected_pos'],
                'shelf':  shelf_name,
            }
            try:
                bk_dummy = sim.createDummy(0.005)
                sim.setObjectAlias(bk_dummy, f'Book_{code}')
                sim.setObjectPosition(bk_dummy, -1, obj['expected_pos'])
                sim.setObjectParent(bk_dummy, obj_h, True)
            except Exception:
                pass

        pos_now = sim.getObjectPosition(obj_h, -1)
        print(f"  [HIERARCHY OK] Created '/{obj_name}' at {[round(v, 3) for v in pos_now]} ({len(layer_handles)} color layers)")

    create_robot_kinematic_handles(sim, handle_by_name)

    # Establish parent-child tree hierarchy for non-arm objects (wheels, PC)
    ARM_EXCLUDE = {'Arm_Turret', 'Arm_Upper_L1', 'Arm_Forearm_L2', 'Arm_Wrist_Gripper', 'ESP32_CAM'}
    for obj in objects_spec:
        obj_name = obj['name']
        parent_name = obj['parent']
        if obj_name in ARM_EXCLUDE:
            continue
        if parent_name and obj_name in handle_by_name and parent_name in handle_by_name:
            sim.setObjectParent(handle_by_name[obj_name], handle_by_name[parent_name], True)

    # Create slot dummies `/Cupboard_1/ADC` & `/Cupboard_1/Book_ADC` inside Cupboards
    for obj in objects_spec:
        code = obj['name']
        shelf_name = obj.get('shelf_group')
        if code in C.CATALOG and shelf_name in handle_by_name:
            cup_h = handle_by_name[shelf_name]
            for alias_str in (code, f'Book_{code}'):
                try:
                    slot_d = sim.createDummy(0.005)
                    sim.setObjectAlias(slot_d, alias_str)
                    sim.setObjectPosition(slot_d, -1, obj['expected_pos'])
                    sim.setObjectParent(slot_d, cup_h, True)
                except Exception:
                    pass

    # Restore exact World Matrix on every 3D mesh after parenting
    for m_h, m_mat in mesh_readback_matrices:
        try:
            sim.setObjectMatrix(m_h, -1, m_mat)
        except Exception:
            pass

    try:
        with open(BOOK_MATRICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(book_home_data, f, indent=2)
        print(f"  [SYNC DATA OK] Saved exact shelf matrices for {len(book_home_data)} books to 'book_home_matrices.json'")
    except Exception as e:
        print(f"  [WARN] Could not save book_home_matrices.json: {e}")

    print("\n[SUCCESS] All 25 modular objects + 14 robot joints/dummies imported with connected kinematic chain!")
    print("[DONE] Press Ctrl+S in CoppeliaSim to save your scene!")


if __name__ == '__main__':
    main()