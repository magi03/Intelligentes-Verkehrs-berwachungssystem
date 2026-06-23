import cv2
import time
import os

# Ordner für die Fotos erstellen
folder = "captured_images"
if not os.path.exists(folder):
    os.makedirs(folder)

# Jetson Nano GStreamer Pipeline 
def get_pipeline(sid):
    return (f"nvarguscamerasrc sensor-id={sid} ! "
            "video/x-raw(memory:NVMM), width=1280, height=720, format=NV12, framerate=30/1 ! "
            "nvvidconv flip-method=2 ! video/x-raw, width=640, height=480, format=BGRx ! "
            "videoconvert ! video/x-raw, format=BGR ! appsink drop=true max-buffers=1")

print(" Initialisiere Kameras...")
cam0 = cv2.VideoCapture(get_pipeline(0), cv2.CAP_GSTREAMER)
cam1 = cv2.VideoCapture(get_pipeline(1), cv2.CAP_GSTREAMER)

if not cam0.isOpened() or not cam1.isOpened():
    print("Fehler: Konnte Kameras nicht starten.")
    exit()

print("\n--- FOTO-MODUS GESTARTET ---")
print("[LEERTASTE] = Foto von beiden Kameras speichern")
print("[Q]         = Programm beenden\n")

img_counter = 0

try:
    while True:
        ret0, frame0 = cam0.read()
        ret1, frame1 = cam1.read()

        if not ret0 or not ret1:
            print("Fehler beim Lesen der Frames.")
            time.sleep(0.1)
            continue

        # Zeige beide Kameras an
        cv2.imshow("Cam 0 (Overview) - Leertaste fuer Foto", frame0)
        cv2.imshow("Cam 1 (Enforcement) - Leertaste fuer Foto", frame1)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            break
        elif key == 32: 
            timestamp = int(time.time())
            
            file0 = os.path.join(folder, f"cam0_{timestamp}_{img_counter}.jpg")
            file1 = os.path.join(folder, f"cam1_{timestamp}_{img_counter}.jpg")
            
            cv2.imwrite(file0, frame0)
            cv2.imwrite(file1, frame1)
            
            print(f"GESPEICHERT: {file0} und {file1}")
            img_counter += 1

finally:
    print("Räume auf...")
    cam0.release()
    cam1.release()
    cv2.destroyAllWindows()