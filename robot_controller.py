import sys
import os
import time
import math

possible_paths = [
    r"C:\Program Files\CoppeliaRobotics\CoppeliaSimEdu\programming\zmqRemoteApi\clients\python",
    r"C:\Program Files\CoppeliaRobotics\CoppeliaSimPlayer\programming\zmqRemoteApi\clients\python",
]

for p in possible_paths:
    if os.path.exists(p):
        sys.path.append(p)
        break

try:
    from zmqRemoteApi import RemoteAPIClient
except ImportError:
    from coppeliasim_zmq_remote_api_client import RemoteAPIClient


class YouBot:
    def __init__(self):
        print("Connecting to CoppeliaSim...")
        self.client = RemoteAPIClient()
        self.sim = self.client.require('sim')

        self.bot_base = self.sim.getObject('/youBot/youBot_ref')

        # Wheel Handles
        self.w_fl = self.sim.getObject('/youBot/rollingJoint_fl')
        self.w_rl = self.sim.getObject('/youBot/rollingJoint_rl')
        self.w_rr = self.sim.getObject('/youBot/rollingJoint_rr')
        self.w_fr = self.sim.getObject('/youBot/rollingJoint_fr')

        # Arm & Gripper Handles
        self.arm1 = self.sim.getObject('/youBot/arm_joint_1')
        self.arm2 = self.sim.getObject('/youBot/arm_joint_2')
        self.arm3 = self.sim.getObject('/youBot/arm_joint_3')
        try:
            self.arm4 = self.sim.getObject('/youBot/arm_joint_4')
        except:
            self.arm4 = -1

        self.j1 = self.sim.getObject('/youBot/finger_joint_1')
        self.j2 = self.sim.getObject('/youBot/finger_joint_2')
        self.gripper_base = self.sim.getObject('/youBot/gripper_base')

        self.speed_lin = 1.6
        self.speed_rot = 1.1
        self.is_grasped = False
        self.current_grasped_book = None
        self.stop_base()
        print("YouBot hardware ready.")

    def find_target(self, name):
        """Resolves target handle for books (SS, NT, EDC, ADC, AC) or Cupboards"""
        # Hierarchy specific search
        search_paths = [
            f'/Cupboard_1/{name}',
            f'/{name}',
            f'::/{name}',
            f'/Cupboard_2/{name}'
        ]
        for p in search_paths:
            try:
                h = self.sim.getObject(p)
                if h != -1:
                    return h
            except:
                pass

        if "Cupboard_2" in name:
            for p in ['/Cupboard_2/Cuboid[0]', '/Cupboard_2/Cuboid', '/Cupboard_2']:
                try:
                    h = self.sim.getObject(p)
                    if h != -1:
                        pos = self.sim.getObjectPosition(h, -1)
                        if abs(pos[0]) > 0.05 or abs(pos[1]) > 0.05:
                            return h
                except:
                    pass

        return -1

    def stop_base(self):
        for w in [self.w_fl, self.w_rl, self.w_rr, self.w_fr]:
            self.sim.setJointTargetVelocity(w, 0.0)

    def move(self, vx, vy, vrot, duration):
        self.sim.setJointTargetVelocity(self.w_fl,  vx - vy - vrot)
        self.sim.setJointTargetVelocity(self.w_rl,  vx + vy - vrot)
        self.sim.setJointTargetVelocity(self.w_rr, -vx + vy - vrot)
        self.sim.setJointTargetVelocity(self.w_fr, -vx - vy - vrot)
        time.sleep(duration)
        self.stop_base()

    def navigate_to(self, target_name):
        target = self.find_target(target_name)
        if target == -1:
            print(f"[Nav Error] Could not find handle for target '{target_name}'!")
            return

        p_tar = self.sim.getObjectPosition(target, -1)
        print(f"[Nav] Driving directly to {target_name} at [{p_tar[0]:.2f}, {p_tar[1]:.2f}]...")

        # Book ke samne close distance 0.35m, Cupboard_2 ke samne 0.50m
        is_cupboard = "Cupboard" in target_name
        stop_dist = 0.50 if is_cupboard else 0.35

        for _ in range(160):
            p_bot = self.sim.getObjectPosition(self.bot_base, -1)
            p_tar = self.sim.getObjectPosition(target, -1)
            ori_bot = self.sim.getObjectOrientation(self.bot_base, -1)

            dx = p_tar[0] - p_bot[0]
            dy = p_tar[1] - p_bot[1]
            dist = math.hypot(dx, dy)

            if dist <= stop_dist:
                print(f"[Nav] Arrived at {target_name} ({dist:.2f} m).")
                break

            target_angle = math.atan2(dy, dx)
            yaw = ori_bot[2]
            err_yaw = (target_angle - yaw + math.pi) % (2 * math.pi) - math.pi

            if abs(err_yaw) > 0.10:
                vrot = self.speed_rot if err_yaw > 0 else -self.speed_rot
                self.move(0, 0, vrot, 0.05)
            else:
                self.move(self.speed_lin, 0, 0, 0.06)

        self.stop_base()

    def set_arm(self, a1, a2, a3, a4=None, duration=1.2):
        self.sim.setJointTargetPosition(self.arm1, a1)
        self.sim.setJointTargetPosition(self.arm2, a2)
        self.sim.setJointTargetPosition(self.arm3, a3)
        if self.arm4 != -1 and a4 is not None:
            self.sim.setJointTargetPosition(self.arm4, a4)
        time.sleep(duration)

    def pick_action(self, book_name="EDC"):
        book = self.find_target(book_name)
        if book == -1:
            print(f"[Error] Book '{book_name}' handle missing in Cupboard_1!")
            return

        # 1. Base joint yaw dynamically aimed at target book
        p_rel = self.sim.getObjectPosition(book, self.arm1)
        arm_yaw = math.atan2(p_rel[1], p_rel[0])

        # Open fingers
        self.sim.setJointPosition(self.j1, -20 * math.pi / 180)
        self.sim.setJointPosition(self.j2, 20 * math.pi / 180)

        # 2. Reach forward into shelf at book position
        print(f"[Action] Reaching for book '{book_name}' at {math.degrees(arm_yaw):.1f}°...")
        self.set_arm(arm_yaw, 0.70, 0.65, a4=0.25, duration=1.5)

        # 3. Close fingers
        print("[Action] Clamping gripper fingers...")
        self.sim.setJointPosition(self.j1, 18 * math.pi / 180)
        self.sim.setJointPosition(self.j2, -18 * math.pi / 180)
        time.sleep(0.4)

        p_grip = self.sim.getObjectPosition(self.gripper_base, -1)
        p_book = self.sim.getObjectPosition(book, -1)
        dist = math.sqrt(sum((p_grip[i] - p_book[i])**2 for i in range(3)))
        print(f"[Sensor] Distance to '{book_name}': {dist:.3f} m")

        # 4. Lock cleanly inside fingers
        if dist <= 0.45:
            self.sim.setObjectParent(book, self.gripper_base, False)
            self.sim.setObjectPosition(book, self.gripper_base, [0.0, 0.10, 0.0])
            self.sim.setObjectOrientation(book, self.gripper_base, [0.0, 0.0, 0.0])
            self.sim.setObjectInt32Param(book, self.sim.shapeintparam_static, 1)
            self.sim.resetDynamicObject(book)
            self.is_grasped = True
            self.current_grasped_book = book
            print(f"[Physics] Book '{book_name}' securely grasped between fingers.")
        else:
            print(f"[Warning] Grip missed: distance was {dist:.2f}m.")

        # 5. Lift arm upright
        self.set_arm(0.0, 0.0, 0.0, a4=0.0, duration=1.2)

    def drop_action(self):
        print("[Action] Reaching shelf for drop...")
        self.set_arm(0.0, 0.65, 0.65, a4=0.20, duration=1.4)

        angle = -20 * math.pi / 180
        self.sim.setJointPosition(self.j1, angle)
        self.sim.setJointPosition(self.j2, -angle)
        time.sleep(0.4)

        if self.current_grasped_book and self.is_grasped:
            self.sim.setObjectParent(self.current_grasped_book, -1, True)
            self.sim.setObjectInt32Param(self.current_grasped_book, self.sim.shapeintparam_static, 0)
            self.sim.resetDynamicObject(self.current_grasped_book)
            self.is_grasped = False
            self.current_grasped_book = None
            print("[Physics] Book successfully placed on destination shelf.")

        self.set_arm(0.0, 0.0, 0.0, a4=0.0, duration=1.0)