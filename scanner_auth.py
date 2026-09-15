import cv2
from pyzbar.pyzbar import decode
import time

AUTHORIZED_ID = "S2500023"

def authenticate_user(stream_url="http://192.168.1.15:81/stream"):
    print(f"\n[Security] Connecting to stream: {stream_url}...")
    cap = cv2.VideoCapture(stream_url)
    
    # Agar ESP32 stream nahi mila toh webcam (0) fallback
    if not cap.isOpened():
        print("[Security] ESP32 stream unavailable. Falling back to local webcam (0)...")
        cap = cv2.VideoCapture(0)

    if not cap.isOpened():
        print("[Security Error] No camera accessible.")
        return False

    print(f"[Security] Scan your ID Card (Target: {AUTHORIZED_ID}) to unlock...")
    authenticated = False

    while not authenticated:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue

        decoded_objects = decode(frame)
        for obj in decoded_objects:
            scanned_data = obj.data.decode('utf-8').strip()
            print(f"[Scanner] Detected: '{scanned_data}'")

            if scanned_data == AUTHORIZED_ID:
                print(f"[Security] Access GRANTED for {scanned_data}!")
                authenticated = True
                break
            else:
                print(f"[Security] Rejected: {scanned_data}")

        cv2.putText(frame, "Scan Student ID to Unlock", (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        cv2.imshow("ID Verification", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    return authenticated