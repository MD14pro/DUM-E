"""
LibBot v4 - saari tuning yahin. Koi magic number kisi aur file me nahi.

Design rule: arm ki link lengths yahan define hoti hai, build_scene.py wahi
numbers sim me banata hai, aur robot_controller.py wahi numbers closed-form
IK me use karta hai. Teeno hamesha sync rehte hai.

Frame convention (har jagah): robot ref frame = floor pe, arm axis ke theek
neeche. +x aage, +y left, +z upar. Saari heights floor se measure hoti hai.
"""
import math

# --------------------------------------------------------------- scene names
ROBOT_NAME = 'LibBot'
SOURCE_SHELF = 'Cupboard_1'
RETURN_SHELF = 'Cupboard_2'
DROP_SPOT = '/Drop_Spot'
DROP_TABLE = '/Drop_Table'

CATALOG = {
    'AC':  {'title': 'Analog Circuits'},
    'EDC': {'title': 'Electronic Devices and Circuits'},
    'NT':  {'title': 'Network Theory'},
    'ADC': {'title': 'Analog and Digital Communication'},
}
# Scene me SS (Signals and Systems) hai hi nahi - sirf ye 4 books hai.

BOOK_SIZE = (0.12, 0.04, 0.16)      # x, y, z (scene se)
BOOK_MASS = 0.18
BOOK_FRICTION = 0.9
FINGER_FRICTION = 1.2

# Har shelf/table ka "saamne" kis world direction me hai (robot wahin khada hota hai)
FRONT = {
    'Cupboard_1': (1.0, 0.0),
    'Cupboard_2': (1.0, 0.0),
    'Drop_Table': (-1.0, 0.0),
}
# Deewarein (xmin, xmax, ymin, ymax). Router inko bacha kar chalta hai.
WALLS = [(-0.10, 0.00, 0.08, 2.08), (-0.10, 0.00, -0.95, 0.05)]
HUB = (-1.10, -1.73)                 # waypoint jahan se sab raaste khule hai
SPAWN = (-1.10, -1.73, math.pi)      # x, y, heading (Cupboard_1 ki taraf mooh)

# --------------------------------------------------------------- body (30x30)
# Footprint ~30 x 30 cm: body 30 x 20 cm, aur dono side original mecanum wheels
# (youBot ke wheels 0.8x scale = 80 mm, width ~4 cm, y = +-12.5 cm -> outer edge 14.5 cm).
BODY_SIZE = (0.30, 0.20, 0.07)
BODY_BOTTOM_Z = 0.025
BODY_TOP_Z = BODY_BOTTOM_Z + BODY_SIZE[2]          # 0.095
BODY_MASS = 7.0
FOOTPRINT = 0.30
BUMPER = FOOTPRINT / 2                              # 0.15

# Wheels: CoppeliaSim ke KUKA YouBot model ke ORIGINAL mecanum wheels (asli roller
# physics) - build_scene.py unhe clone karke 0.8x scale karta hai (100 mm -> 80 mm).
WHEEL_DIAMETER = 0.080                              # <= 80 mm (constraint)
WHEEL_RADIUS = WHEEL_DIAMETER / 2
WHEEL_SOURCE_DIAMETER = 0.100                       # youBot wheel
WHEEL_X = 0.10                                      # wheelbase 20 cm
WHEEL_Y = 0.125                                     # track 25 cm
WHEEL_FORCE = 3.0                                   # N*m per wheel motor
MECANUM_K = WHEEL_X + WHEEL_Y
# Wheels ko drive karne ka sign (forward, strafe, rotate). calibrate_base khud
# theek kar deta hai aur batata hai yahan kya likhna hai.
DRIVE_SIGNS = [1.0, 1.0, 1.0]
YOUBOT_MODEL = None     # custom path: python build_scene.py --youbot-model "C:\\...\\KUKA YouBot.ttm"
CAM_RES = (320, 240)
CAM_FOV_DEG = 60.0

# --------------------------------------------------------------- arm (4-DOF)
# yaw -> shoulder pitch -> elbow pitch -> wrist pitch, phir parallel gripper.
# Arm body ke theek upar centre me baithta hai: floating nahi, turret body
# pe seedha tika hai (Z_YAW == BODY_TOP_Z).
TURRET_H = 0.06
Z_YAW = BODY_TOP_Z
Z_SHOULDER = Z_YAW + TURRET_H                       # 0.155
ARM_L1 = 0.20
ARM_L2 = 0.18
ARM_L3 = 0.12                                       # wrist joint -> grasp point
# (min, max) radians. th = floor se upar positive, planar angle.
ARM_LIMITS = {'yaw': (-2.8, 2.8), 'th1': (-0.6, 2.4),
              'th2': (-2.9, 0.4), 'th3': (-2.2, 2.2)}
ARM_JOINT_FORCE = 25.0
CARRY_TIP = (0.22, 0.00, 0.23)                      # base frame (x, y, z), driving pose

GRIP_OPEN = 0.065                                   # har finger ka travel
GRIP_FORCE = 30.0
GRIP_MIN_HOLD = 0.006                               # isse kam khula = kuch pakda nahi
FINGER_LEN, FINGER_T, FINGER_H = 0.06, 0.012, 0.03

# --------------------------------------------------------------- grasp tuning
GRASP_DEPTH = 0.035        # book centre se robot ki taraf itna aage pakdo
GRASP_Z_OFFSET = 0.04      # book centre se itna upar pakdo (palm body se upar rahe)
PRE_GRASP_OFFSET = 0.07
LIFT_HEIGHT = 0.05
RELEASE_GAP = 0.01
GRASP_MODE = 'rigid'       # 'rigid' = clamp + weld (reliable) | 'friction' = pure physics

APPROACH_STANDOFF = 0.34   # robot centre -> book centre (shelf). 0.30 pe arm joint limit pe jam ho jaata tha (margin 0.02 rad), 0.34 pe 0.32 rad
PICK_TABLE_STANDOFF = 0.30 # robot centre -> book centre (table se uthana)
RETURN_SHELF_PLACE_Z = 0.12   # Cupboard_2 me book ka centre kis height pe rakhna hai

# --------------------------------------------------------------- drop table
TABLE_SIZE = (0.30, 0.60, 0.03)    # depth (x), width (y), top thickness
TABLE_TOP_Z = 0.12
TABLE_LEG = 0.04
TABLE_COLOR = [0.55, 0.38, 0.22]
TABLE_STANDOFF = 0.28               # Robot centre -> table centre (decreased from 0.35)
TABLE_INSET = 0.15                      # Book near-edge se itna andar (increased from 0.10)
TABLE_SLOTS = [-0.20, -0.07, 0.07, 0.20]   # table pe books ke lateral positions

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
