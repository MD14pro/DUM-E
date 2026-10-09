# LibBot - Autonomous Library Station

CoppeliaSim me library robot: ID card scan -> web UI se book choose -> bot shelf se
uthaakar drop table pe rakhta hai -> return pe Cupboard_2 me wapas.

## Pehli baar (ek hi baar)

```cmd
pip install -r requirements.txt
```
1. CoppeliaSim me **original lib2.ttt** kholo. Simulation **STOPPED**.
2. `python build_scene.py`  -> robot + drop table + books conditioning, sab ek command me.
   Wheels CoppeliaSim ke **KUKA YouBot model ke original mecanum wheels** se bante hai (0.8x = 80 mm).
   Model nahi mila to error batayega: Model browser > Robots > Mobile > KUKA YouBot ko scene me
   drag karke script dobara chalao, ya `--youbot-model "<path>\KUKA YouBot.ttm"`.
3. **File > Save Scene As...** (original overwrite mat karna).
4. Simulation **START**, phir `python check_setup.py`.
   Sab OK aaye to aage. FAIL aaye to poora output mujhe bhej do.
5. Khali jagah pe: `python main_system.py --calibrate --no-auth`

## Roz ka run
Scene open -> Simulation START -> **`run.bat`** (backend + browser + robot worker teeno khul jaate hai).

Manual: `python -m uvicorn backend.app:app --port 8000` aur `python main_system.py --mode worker`.
Browser me member select -> book pe **Fetch to desk**.

## Tests (CoppeliaSim ke bina)
`python selftest.py` - fake sim pe poora mission (fetch, place, return, AC upar wali shelf, wall routing).

## Robot
| | |
|---|---|
| footprint | 30 x 30 cm (body 30x24 + wheels) |
| wheels | youBot ke ORIGINAL mecanum wheels x4, 0.8x scale = 80 mm, asli roller physics |
| camera | `gripper_cam` palm pe, aage dekhti hai (320x240). Live: `python camera_view.py` |
| arm | yaw + 3 pitch (4-DOF), L1 20 cm, L2 18 cm, gripper tip 12 cm |
| arm base | body ke theek upar turret pe tika hua, floating nahi |
| gripper | parallel, dynamic fingers, 30 N, 65 mm travel each |

Base ka physics: wheels khud body ko chalate hai (wheel velocity, accel-limited). Har wheel joint
ka axis sign sim se padha jaata hai, aur `--calibrate` forward/strafe/rotate asli sim me test karke
`DRIVE_SIGNS` auto-fix karta hai.

## Kya verified hai, kya nahi
**Verify ho chuka (offline):** IK round trip (0.0000 mm), har book/table/Cupboard_2 reachable,
joint-limit margin >= 0.3 rad, poora mission flow, backend, planner, wall routing.
**Sirf real sim me check hoga (`check_setup.py` karta hai):** FK vs asli tip, arm sag,
body tilt, wheels ki jagah, asli driving (forward/strafe/rotate), camera.

## Tuning (sab `config.py` me)
| Problem | Badlo |
|---|---|
| Bot shelf se takrata hai / arm limit pe jam | `APPROACH_STANDOFF` (0.34; 0.30 pe margin 0.02 rad tha) |
| Book Cupboard_2 me nahi ghusti | `RETURN_SHELF_PLACE_Z` |
| Bot ulta/side me chalta hai | `python main_system.py --calibrate --no-auth` (auto-fix), phir `DRIVE_SIGNS` |
| Bot dheere / wheel slip | `WHEEL_FORCE`, `LIN_ACCEL`, `MAX_LIN_SPEED` |
| Arm sag / dheela | `ARM_JOINT_FORCE` |
| Book fisalti hai | `GRIP_FORCE`, `BOOK_FRICTION`, ya `GRASP_MODE='rigid'` |
| Robot galat taraf mooh | `SPAWN` heading, `FRONT` (shelf kis taraf face karti hai) |

## Files
config.py, kinematics.py, robot_controller.py, build_scene.py, check_setup.py, camera_view.py, selftest.py,
llm_agent.py, scanner_auth.py, main_system.py, backend/{app,db}.py, backend/static/index.html
