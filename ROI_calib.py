import cv2
import numpy as np

# Jetson Nano GStreamer Pipeline
def get_pipeline(sid):
    return (f"nvarguscamerasrc sensor-id={sid} ! "
            "video/x-raw(memory:NVMM), width=1280, height=720, format=NV12, framerate=30/1 ! "
            "nvvidconv flip-method=2 ! video/x-raw, width=640, height=480, format=BGRx ! "
            "videoconvert ! video/x-raw, format=BGR ! appsink drop=true max-buffers=1")

# (0 = Overview, 1 = Enforcement)
CAMERA_ID = 0 

print(f"Nehme ein Foto von Kamera {CAMERA_ID} auf...")
cam = cv2.VideoCapture(get_pipeline(CAMERA_ID), cv2.CAP_GSTREAMER)
ret, frame = cam.read()
cam.release()

if not ret:
    print("Fehler: Konnte kein Bild aufnehmen.")
    exit()

# Variablen für das Zeichnen
points = []
image_copy = frame.copy()

def mouse_callback(event, x, y, flags, param):
    global points, image_copy
    
    # Linksklick: Punkt hinzufügen
    if event == cv2.EVENT_LBUTTONDOWN:
        points.append([x, y])
    
    # Rechtsklick: Letzten Punkt entfernen
    elif event == cv2.EVENT_RBUTTONDOWN:
        if len(points) > 0:
            points.pop()

    # Bild neu zeichnen
    image_copy = frame.copy()
    
    # Zeichne alle Punkte und verbinde sie
    if len(points) > 0:
        for p in points:
            cv2.circle(image_copy, tuple(p), 5, (0, 0, 255), -1)
            
        if len(points) > 1:
            cv2.polylines(image_copy, [np.array(points)], isClosed=False, color=(0, 255, 0), thickness=2)
            
        # Wenn mindestens 3 Punkte da sind, schließe das Polygon provisorisch
        if len(points) > 2:
            cv2.line(image_copy, tuple(points[-1]), tuple(points[0]), (0, 255, 0), 2)
            
    cv2.imshow("ROI Kalibrierung", image_copy)

# Fenster setup
cv2.namedWindow("ROI Kalibrierung")
cv2.setMouseCallback("ROI Kalibrierung", mouse_callback)

print("\n---ROI ZEICHNEN GESTARTET ---")
print("Linksklick  = Punkt setzen")
print("Rechtsklick = Letzten Punkt loeschen")
print("[S] drücken = ROI speichern und Code generieren")
print("[Q] drücken = Beenden ohne speichern\n")

# Erstes Bild anzeigen
cv2.imshow("ROI Kalibrierung", image_copy)

while True:
    key = cv2.waitKey(1) & 0xFF
    
    if key == ord('q'):
        print("Abgebrochen.")
        break
        
    elif key == ord('s'):
        if len(points) < 3:
            print("Ein Polygon braucht mindestens 3 Punkte!")
        else:
            points_str = ", ".join([f"[{p[0]}, {p[1]}]" for p in points])
            print("\nFERTIG! Kopiere diesen Code in dein Hauptskript:\n")
            print(f"np.array([{points_str}], np.int32)")
            print("\n")
            break

cv2.destroyAllWindows()