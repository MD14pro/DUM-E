import json
from scanner_auth import authenticate_user
from robot_controller import YouBot
from llm_agent import get_llm_plan, execute_plan

def main():
    print("==================================================")
    print("   Autonomous Library youBot Station (AUTH LOCKED)")
    print("==================================================")

    # ESP32-CAM stream URL (agar local webcam se test karna ho toh authenticate_user(0) pass karein)
    esp32_cam_url = "http://192.168.1.15:81/stream"
    
    # 1. Barcode/QR Verification (Requires ID: 'S2500023')
    is_authenticated = authenticate_user(stream_url=0)
    if not is_authenticated:
        print("[System Locked] Unverified card. Operation aborted.")
        return

    # 2. Initialize Robot on Success
    bot = YouBot()
    
    print("\n==================================================")
    print("  ACCESS GRANTED! Available Subjects in Cupboard_1:")
    print("  1. SS   (Signals and Systems)")
    print("  2. NT   (Network Theory)")
    print("  3. EDC  (Electronic Devices and Circuits)")
    print("  4. ADC  (Analog & Digital Communication)")
    print("  5. AC   (Analog Circuits)")
    print("==================================================")

    while True:
        try:
            cmd = input("\nEnter Task Command > ").strip()
            if not cmd:
                continue
            if cmd.lower() in ["exit", "quit", "lock"]:
                break

            plan = get_llm_plan(cmd)
            print("Generated JSON Plan:\n", json.dumps(plan, indent=2))

            if plan and isinstance(plan, list):
                execute_plan(bot, plan)
            else:
                print("[Error] Could not resolve a valid action plan.")

        except KeyboardInterrupt:
            break

if __name__ == "__main__":
    main()