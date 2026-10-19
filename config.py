"""
config.py
=========
LibBot v4 (DUM-E) Configuration and Hardware Specifications.
"""
import math

# --------------------------------------------------------------- scene names
ROBOT_NAME = 'LibBot'
SOURCE_SHELF = 'Cupboard_1'
RETURN_SHELF = 'Cupboard_2'
DROP_SPOT = '/Drop_Spot'
DROP_TABLE = '/Drop_Table'

CATALOG = {
    'AC':   {'title': 'Analog Circuits'},
    'EDC':  {'title': 'Electronic Devices and Circuits'},
    'NT':   {'title': 'Network Theory'},
    'ADC':  {'title': 'Analog and Digital Communication'},
    'FOC':  {'title': 'Fiber Optic Communications'},
    'RASC': {'title': 'Radar and Satellite Communications'},
    'ES':   {'title': 'Embedded Systems'},
    'CS':   {'title': 'Control Systems'},
    'IOT':  {'title': 'Internet of Things'},
    'CN':   {'title': 'Computer Networks'},
}

BOOK_SIZE = (0.15, 0.032, 0.195)    # x (depth), y (width), z (height)
BOOK_MASS = 0.18
BOOK_FRICTION = 0.9
FINGER_FRICTION = 1.2

# Front approach vectors
FRONT = {
    'Cupboard_1': (-1.0, 0.0),
    'Cupboard_2': (-1.0, 0.0),
    'Drop_Table': (1.0, 0.0),
}
WALLS = []
HUB = (0.0, 0.0)                     # center corridor waypoint
SPAWN = (0.0, 0.0, 0.0)

# --------------------------------------------------------------- body (30x30)
BODY_SIZE = (0.30, 0.20, 0.07)
BODY_BOTTOM_Z = 0.025
BODY_TOP_Z = BODY_BOTTOM_Z + BODY_SIZE[2]          # 0.095
BODY_MASS = 15.0
FOOTPRINT = 0.30
BUMPER = FOOTPRINT / 2                              # 0.15

WHEEL_DIAMETER = 0.080                              # <= 80 mm (constraint)
WHEEL_RADIUS = WHEEL_DIAMETER / 2
WHEEL_SOURCE_DIAMETER = 0.100                       # youBot wheel
WHEEL_X = 0.10                                      # wheelbase 20 cm
WHEEL_Y = 0.125                                     # track 25 cm
WHEEL_FORCE = 3.0                                   # N*m per wheel motor
MECANUM_K = WHEEL_X + WHEEL_Y
DRIVE_SIGNS = [1.0, 1.0, 1.0]
YOUBOT_MODEL = None
CAM_RES = (320, 240)
CAM_FOV_DEG = 60.0

# --------------------------------------------------------------- arm (4-DOF)
TURRET_H = 0.054
Z_YAW = BODY_TOP_Z                                  # 0.094
Z_SHOULDER = Z_YAW + TURRET_H                       # 0.148
ARM_L1 = 0.20
ARM_L2 = 0.18
ARM_L3 = 0.12                                       # wrist joint -> grasp point
ARM_LIMITS = {
    'yaw': (-2.8, 2.8),
    'th1': (-0.6, 2.4),
    'th2': (-2.9, 0.4),
    'th3': (-2.2, 2.2)
}
ARM_JOINT_FORCE = 25.0
CARRY_TIP = (0.22, 0.00, 0.25)                      # base frame (x, y, z), high carry pose

GRIP_OPEN = 0.065                                   # travel per finger
GRIP_FORCE = 30.0
GRIP_MIN_HOLD = 0.006
FINGER_LEN, FINGER_T, FINGER_H = 0.06, 0.012, 0.03

# --------------------------------------------------------------- grasp tuning
GRASP_DEPTH = 0.035
GRASP_Z_OFFSET = 0.04
PRE_GRASP_OFFSET = 0.07
LIFT_HEIGHT = 0.05
RELEASE_GAP = 0.01
GRASP_MODE = 'rigid'

APPROACH_STANDOFF = 0.34
PICK_TABLE_STANDOFF = 0.32
RETURN_SHELF_PLACE_Z = 0.36

# --------------------------------------------------------------- drop table
# Drop Table is at X = -1.85m, Y = 0.00m.
# The PC Monitor & Tower occupy Y from -0.30m to +0.05m.
# The dedicated orange "PICKUP / DROP ZONE" mat is at positive Y (+0.08m to +0.29m, center Y = +0.185m).
TABLE_SIZE = (0.42, 0.72, 0.28)
TABLE_TOP_Z = 0.282
TABLE_STANDOFF = 0.32
TABLE_INSET = 0.15
# All slots are placed on the dedicated orange mat, completely clear of the PC!
TABLE_SLOTS = [0.12, 0.16, 0.20, 0.24]
TABLE_APPROACH_Y = 0.185

# --------------------------------------------------------------- base motion
MAX_LIN_SPEED = 0.35
MAX_STRAFE_SPEED = 0.25
MAX_ROT_SPEED = 1.0
LIN_ACCEL = 0.30
ROT_ACCEL = 1.0
POS_GAIN = 1.8
HEADING_GAIN = 2.2
POS_TOLERANCE = 0.03
YAW_TOLERANCE = 0.04
NAV_TIMEOUT = 60.0
WALL_MARGIN = 0.30

SIM_TIME_STEP = 0.01
SETTLE_STEPS = 60

# --------------------------------------------------------------- services
BACKEND_URL = 'http://127.0.0.1:8000'
OLLAMA_URL = 'http://localhost:11434/api/generate'
OLLAMA_MODEL = 'llama3.2:3b'
OLLAMA_TIMEOUT = 30
ESP32_CAM_URL = 'http://192.168.1.15:81/stream'
SCAN_TIMEOUT = 60
SCAN_COOLDOWN = 1.5
FALLBACK_MEMBER_ID = 'S2500023'