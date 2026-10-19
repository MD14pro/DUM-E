# DUM-E [DELIVERY, UTILITY, MANAGEMENT AND EXCHANGE:BOOKS]
🤖 DUM-E Autonomous Library Station (v1.4)

An End-to-End Autonomous Mobile Manipulator (4-Mecanum Wheel KUKA youBot + 4-DOF Robotic Arm + ESP32-CAM Optical Vision) for Smart Library Book Issue, Return, Student I-Card Authentication & Real-Time Inventory Management.

Built with CoppeliaSim (ZeroMQ Remote API), FastAPI, SQLite, OpenCV / PyZbar, and OpenPyXL.

🌟 Key Features

🪪 Step-1 Student I-Card Authentication (Multi-Modal Auth)

Web Kiosk Input & USB Barcode Scanner: Instant verification of student I-Card IDs (e.g., S2500023 — Muddassir Sheikh, 4th Year ENTC) on the self-service web kiosk.

Live Optical Camera QR/Barcode Scanner: Built-in browser webcam and host/ESP32-CAM optical decoding (pyzbar + cv2.QRCodeDetector + cv2.barcode.BarcodeDetector fallback) so it works out-of-the-box on any Windows/Linux PC without external DLL headaches.

On-the-Spot Student Registration: Register new student I-Cards with Name, Academic Year (1st–4th), and Engineering Branch (ENTC, CSE, EE, ME, CE).

🦾 Autonomous 4-DOF Mobile Manipulation in CoppeliaSim

Omnidirectional Mecanum Drive (50 Hz): Smooth, wall-clock-synchronized trajectory tracking (0.35 m/s linear, 0.25 m/s strafe, 1.0 rad/s yaw) with real-time 4-wheel visual rolling.

Unbreakable 4-DOF Robotic Arm Kinematics: Articulates turret yaw, shoulder, elbow, and wrist relative to the CAD rest pose to pick books from upper (z = 0.557m) and lower (z = 0.357m) cupboard shelves.

High-Altitude Book Carrying (z = 0.35m): Suspends grasped books safely above the floor and table edges during corridor transit.

Dedicated Drop-Table Delivery Zone: Places issued books onto the orange PICKUP / DROP ZONE mat (X = -1.73m, Y = +0.12m..+0.24m), completely clear of the desktop PC workstation.

📹 Hands-Free Robot POV (ESP32-CAM) & Auto-Book Scanner

Live 320×240 @ 15 FPS MJPEG stream from the wrist-mounted vision sensor (/LibBot/Arm_Wrist_Gripper/gripper_cam) with a green targeting HUD overlay.

Continuous background optical barcode/QR polling every 1.8s that automatically detects books in front of the gripper and populates the kiosk search bar.

🔐 Password-Protected Librarian Admin Panel

Protected by librarian credentials (default password: admin123).

Add new subject titles, adjust physical copy counts (+1 Copy / -1 Copy), and relocate books between Cupboard_1 and Cupboard_2 in real time.

📊 Real-Time Tri-Workbook Excel Synchronization & Penalty Engine

Automatically generates and updates three formatted Excel workbooks on every transaction:

available_books.xlsx — Live stock counts, shelf locations, and borrow frequency.

Users.xlsx — Student directory, open loan counts, and accumulated overdue fines.

library_logs.xlsx — 3-sheet audit workbook (Live Books State, Transaction Audit Logs, and Missions History).

Automatic overdue fine calculation (₹1.00 / day after the 14-day loan window).

📁 Repository Structure (What Goes on GitHub)

Below is the clean, portable file structure required to run the entire project on any PC:

File / Folder

Role & Description

NEW-DUM-E.ttt

Main CoppeliaSim 3D Scene containing LibBot (kinematic dummy root), Cupboard_1, Cupboard_2, all 10 3D books, Drop_Table, and Corridor_Track.

run.bat

One-Click Windows Launcher — syncs the 3D scene with library.db and launches the unified FastAPI Web Kiosk + Robot Orchestrator.

main_system.py

Unified Orchestrator & CLI (v2.6) — starts the embedded FastAPI server (http://127.0.0.1:8000/), enforces Student I-Card Auth, connects YouBot to CoppeliaSim, and runs the task > CLI.

robot_controller.py

Core Robot Controller (YouBot) — thread-safe ZeroMQ client (RLock), 50 Hz Mecanum controller, 4-DOF arm articulation, 4 Hz Web Kiosk mission worker, and ESP32-CAM frame grabber.

app.py

FastAPI Web Backend — REST endpoints for Student I-Card Auth, Issue/Return queue, Librarian Admin Panel, MJPEG camera streaming, and .xlsx downloads.

db.py

SQLite & Excel Data Layer — manages library.db, multi-copy inventory, strict per-student loan isolation, ₹1/day penalty engine, optical frame decoder, and openpyxl sync.

index.html

Smart Student Kiosk & Admin UI — 3-step self-service kiosk, live SVG floor map with robot telemetry trail, ESP32-CAM live feed, and collapsible Librarian Admin Panel.

scanner_auth.py

Optical I-Card Scanner — decodes QR codes and 1D barcodes via ESP32-CAM or local webcam using pyzbar and OpenCV fallbacks.

config.py

Hardware & Scene Configuration — catalog metadata, Mecanum geometry, 4-DOF arm link lengths, approach standoffs, and network ports.

kinematics.py

Mathematical Kinematics Engine — 4-DOF planar/spatial forward & inverse kinematics (solve_ik) and Mecanum wheel velocity equations.

sync_simulation.py

3D Scene & DB Synchronizer — aligns all 10 books in CoppeliaSim with their current library.db state (Cupboard_1, Cupboard_2, or Drop_Table) on startup.

import_dume_to_coppelia.py

Modular 3D Scene Importer & Auto-Repair — rebuilds the 25 multi-color 3D objects and kinematic joint chain from DUM-E mark 1.stl if needed.

book_home_matrices.json

3D Calibration Data — exact 4×3 world transformation matrices and shelf centers for all 10 books.

requirements.txt

Python Dependencies — complete package list for one-command pip install -r requirements.txt.

.gitignore

Git Ignore Rules — excludes auto-generated runtime files (__pycache__/, library.db, *.xlsx, latest_frame.jpg, color_meshes/).

📚 Indexed Engineering Book Catalog & 3D Coordinates

Each subject starts with 3 physical copies (30 total copies across 10 subjects):

Code

Subject Title

Author

Location

Tier

3D Coordinates (X, Y, Z)

AC

Analog Circuits

Sedra & Smith

Cupboard_1

Lower

(1.7405, +0.2310, 0.3575)

EDC

Electronic Devices and Circuits

Boylestad & Nashelsky

Cupboard_1

Lower

(1.7405, +0.3510, 0.3575)

NT

Network Theory

Van Valkenburg

Cupboard_1

Lower

(1.7405, +0.4710, 0.3575)

ADC

Analog and Digital Communication

Simon Haykin

Cupboard_1

Upper

(1.7405, +0.2310, 0.5575)

CS

Control Systems

Norman Nise

Cupboard_1

Upper

(1.7405, +0.3510, 0.5575)

FOC

Fiber Optic Communications

Gerd Keiser

Cupboard_2

Lower

(1.7405, -0.6490, 0.3575)

RASC

Radar and Satellite Communications

Merrill Skolnik

Cupboard_2

Lower

(1.7405, -0.5290, 0.3575)

ES

Embedded Systems

Raj Kamal

Cupboard_2

Lower

(1.7405, -0.4090, 0.3575)

IOT

Internet of Things

Arshdeep Bahga

Cupboard_2

Upper

(1.7405, -0.5290, 0.5575)

CN

Computer Networks

Andrew S. Tanenbaum

Cupboard_2

Upper

(1.7405, -0.4090, 0.5575)

🚀 Getting Started on Any PC (Step-by-Step Setup)

1. Prerequisites

CoppeliaSim Edu / Pro (v4.5, v4.6, v4.7, or v4.8+ with built-in ZeroMQ Remote API on port 23000).

Python 3.10+ (Windows, Linux, or macOS).

2. Clone the Repository & Install Dependencies

git clone https://github.com/<your-username>/DUM-E-Library-Station.git
cd DUM-E-Library-Station
pip install -r requirements.txt


3. Open the 3D Scene in CoppeliaSim

Launch CoppeliaSim.

Go to File → Open Scene... and select NEW-DUM-E.ttt.
(Keep the simulation stopped or running — the Python controller automatically manages simulation start and synchronization).

4. Launch the Station

Option A: One-Click Windows Launch (Recommended)

Double-click run.bat or run from Command Prompt:

run.bat


This automatically:

Synchronizes all 10 3D books in CoppeliaSim with library.db (sync_simulation.py).

Starts the FastAPI Web Kiosk at http://127.0.0.1:8000/ and opens your browser.

Connects the YouBot controller to CoppeliaSim and opens the interactive task > console.

Option B: Manual Terminal Launch

python sync_simulation.py
python main_system.py


(Optional flags: python main_system.py --no-auth to bypass I-Card verification for testing, or python main_system.py --camera-auth to open the OpenCV webcam window on startup).

🖥️ Operating Guide

A. Using the Student Self-Service Web Kiosk (http://127.0.0.1:8000/)

Step 1 — Verify Student I-Card (Required):

Enter or scan your I-Card ID (default registered student: S2500023 — Muddassir Sheikh, 4th Year ENTC) and click ✓ Verify I-Card, or click 📷 Scan I-Card to scan a QR/Barcode via webcam, or click + New Student to register a new card.

Step 2 — Select Operation:

Choose 📥 Issue Book (to fetch a book from the cupboards to the Drop Table) or 📤 Return Book (to return one of your active loans back to its cupboard slot).

Step 3 — Search & Dispatch DUM-E:

Search by subject code (AC, CS, FOC), title (Control Systems), or author (Norman Nise) and click Issue or Return to Cupboard.

Watch DUM-E's live trajectory on the Live Station Floor Map and Robot POV (ESP32-CAM Feed).

B. Using the Interactive Terminal CLI (task >)

You can also control the entire station from the terminal prompt:

task > S2500023        # Verify Student I-Card ID S2500023 (unlocks CLI & Web Kiosk)
task > scan            # Open webcam / ESP32-CAM window to scan I-Card QR/Barcode
task > bring AC        # Dispatch DUM-E to fetch Analog Circuits (AC) to Drop Table
task > return AC       # Dispatch DUM-E to return Analog Circuits (AC) to Cupboard_1
task > bring CS        # Fetch Control Systems (CS) from Cupboard_1 Upper Tier
task > logout          # Lock session until next student verifies I-Card
task > exit            # Shut down station cleanly


C. Using the Librarian Admin Panel

Click ⚙️ Librarian Admin in the top-right bar of the Web Kiosk.

Enter the librarian password: admin123 (also accepts admin, librarian123, dume2026).

Use the panel to:

Add new subject books or update existing book titles/authors.

Click +1 Copy / -1 Copy to adjust physical stock.

Click Move to Cupboard 1 / 2 to reassign shelf locations.

Click 📥 Download available_books.xlsx to export the formatted inventory sheet.

🔌 REST API Summary (http://127.0.0.1:8000/docs)

Method

Endpoint

Description

GET

/api/dashboard

Full station state (books, stats, verified member, robot telemetry, missions, logs).

POST

/api/auth/verify

Verify student I-Card ID ({"member_id": "S2500023"}) and unlock session.

POST

/api/auth/scan-frame

Decode QR/Barcode from base64 browser webcam frame and authenticate student.

POST

/api/auth/logout

Clear active student session and lock kiosk back to Step 1.

POST

/api/members

Register a new student (id, name, year, branch).

POST

/api/issue

Queue a robot fetch mission ({"book_code": "AC", "member_id": "S2500023"}).

POST

/api/return

Queue a robot return mission for an active student loan.

POST

/api/admin/verify

Authenticate Librarian Admin Panel password.

POST

/api/admin/books

Add or update a book's metadata, shelf, and total copies.

GET

/api/camera/stream

Live MJPEG video stream from the robot's wrist vision sensor.

POST

/api/reset

Reset library.db, restore 3 copies per book, and reset 3D books in CoppeliaSim.

🛠️ Troubleshooting

run.bat closes or says cannot connect to CoppeliaSim:
Make sure CoppeliaSim is open with NEW-DUM-E.ttt loaded before running run.bat. CoppeliaSim's ZMQ Remote API listens on localhost:23000 by default.

Books or robot out of place in CoppeliaSim:
Click the Reset DB button on the Web Kiosk (http://127.0.0.1:8000/) or run python sync_simulation.py --reset-db to snap all 10 3D books back to their exact home shelf slots and return LibBot to (0, 0, 0).

Rebuilding the 3D Scene from STL:
If you ever open an empty scene in CoppeliaSim and have DUM-E mark 1.stl in the folder, run python import_dume_to_coppelia.py to automatically construct all 25 multi-color objects and joints.
