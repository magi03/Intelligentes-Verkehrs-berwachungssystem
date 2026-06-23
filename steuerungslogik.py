import torch
import cv2
import time
import json
import os
import threading
import numpy as np
import pandas as pd
import requests
import paho.mqtt.client as mqtt
from flask import Flask, Response
from models.common import DetectMultiBackend
from utils.general import non_max_suppression
import Jetson.GPIO as GPIO
 
 
# --- CONFIGURATION ---
 
MQTT_BROKER = "127.0.0.1" 
MQTT_TOPIC = "traffic/intersection1/data"
 
#  WICHTIG: Trage hier deine Laptop-IP (172.20.10.2) ein!
 
LAPTOP_UPLOAD_URL = "http://172.20.10.2:5000/api/upload_violation"
 
ROIS_COUNTING = { 




    1: np.array([[358, 389], [577, 370], [466, 214], [332, 227]], np.int32), 




    2: np.array([[241, 105], [316, 93], [320, 149], [234, 156]], np.int32), 




    3: np.array([[566, 102], [610, 136], [422, 172], [409, 139]], np.int32), 




    4: np.array([[1, 228], [12, 194], [224, 192], [196, 233]], np.int32) 




} 




ROIS_VIOLATION = { 




    1: np.array([[366, 271], [486, 270], [473, 263], [363, 265]], np.int32), 




    2: np.array([[234, 206], [318, 208], [318, 215], [234, 212]], np.int32), 




    3: np.array([[400, 204], [428, 235], [422, 236], [394, 207]], np.int32), 




    4: np.array([[246, 275], [245, 240], [251, 238], [254, 276]], np.int32) 




} 
 




ROIS_CRASH = { 




    1: np.array([[199, 232], [466, 214], [412, 138], [231, 157]], np.int32), 
} 




 
# --- PHYSICAL GPIO PIN MAPPING ---
 
NS_RED    = 29
NS_YELLOW = 31
NS_GREEN  = 33
 
EW_RED    = 11
EW_YELLOW = 13
EW_GREEN  = 15
 
ALL_PINS = [NS_RED, NS_YELLOW, NS_GREEN, EW_RED, EW_YELLOW, EW_GREEN]
 
print("Initializing Physical GPIO Pins...")
 
GPIO.setmode(GPIO.BOARD)
GPIO.setwarnings(False)
GPIO.setup(ALL_PINS, GPIO.OUT, initial=GPIO.LOW)
 
def update_physical_lights(ns_state, ew_state):
 
   """Updates the physical LEDs using the exact wiring map"""
   # NEU: Rot+Gelb gleichzeitig fuer Rot+Gelb-Phase
   GPIO.output(NS_RED,    GPIO.HIGH if ns_state in ("Rot", "Rot+Gelb") else GPIO.LOW)
   GPIO.output(NS_YELLOW, GPIO.HIGH if ns_state in ("Gelb", "Rot+Gelb") else GPIO.LOW)
   GPIO.output(NS_GREEN,  GPIO.HIGH if ns_state == "Grün" else GPIO.LOW)
 
   GPIO.output(EW_RED,    GPIO.HIGH if ew_state in ("Rot", "Rot+Gelb") else GPIO.LOW)
   GPIO.output(EW_YELLOW, GPIO.HIGH if ew_state in ("Gelb", "Rot+Gelb") else GPIO.LOW)
   GPIO.output(EW_GREEN,  GPIO.HIGH if ew_state == "Grün" else GPIO.LOW)
 
# --- AI TRAFFIC STATE VARIABLES ---
 
current_phase = 0
last_switch = time.time()
crash_active = False   # Merkt sich den Notfall-Status
crash_timer = 0        # Stoppuhr für die 8-Sekunden-Rotsperre
 
MIN_GREEN = 5.0      
YELLOW_TIME = 2.0
ROT_GELB_TIME = 1.5   # NEU: Dauer der Rot+Gelb-Vorwarnphase in Sekunden
NORMAL_TIMER = 15.0  
MAX_WAIT = 50.0      
 
blitzer_counter = {1: 0, 2: 0, 3: 0, 4: 0}
os.makedirs("violations", exist_ok=True)
 
# --- NATIVE MODEL LOADING ---
 
print("Loading Custom YOLOv5 Model directly...")
device = torch.device('cuda:0')
model = DetectMultiBackend('best.pt', device=device, dnn=False, data='data/coco128.yaml', fp16=True)
names = model.names
 
print(f"Loaded Custom Classes: {names}")
 
client = mqtt.Client("Jetson_Master", protocol=mqtt.MQTTv311)
client.connect(MQTT_BROKER, 1883)
client.loop_start()
 
# --- THREADED CAMERA CLASS ---
 
def get_pipeline(sid):
 
   return (f"nvarguscamerasrc sensor-id={sid} ! "
           "video/x-raw(memory:NVMM), width=1280, height=720, format=NV12, framerate=30/1 ! "
           "nvvidconv flip-method=2 ! video/x-raw, width=640, height=480, format=BGRx ! "
           "videoconvert ! video/x-raw, format=BGR ! appsink drop=true max-buffers=1")
 
class ThreadedCamera:
 
   def __init__(self, src=0):
       self.cap = cv2.VideoCapture(get_pipeline(src), cv2.CAP_GSTREAMER)
       self.ret, self.frame = self.cap.read()
       self.running = True
       self.thread = threading.Thread(target=self.update, daemon=True)
       self.thread.start()
 
   def update(self):
       while self.running:
           self.ret, self.frame = self.cap.read()
 
   def read(self):
       return self.ret, self.frame
 
   def release(self):
       self.running = False
       self.thread.join()
       self.cap.release()
 
print("Starting Background Cameras...")
cam_overview = ThreadedCamera(0)
cam_enforce = ThreadedCamera(1)
time.sleep(2) 
 
def is_in_roi(box, roi_polygon):
   cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
   bx, by = cx, box[3] 
   in_center = cv2.pointPolygonTest(roi_polygon, (cx, cy), False) >= 0
   in_bottom = cv2.pointPolygonTest(roi_polygon, (bx, by), False) >= 0
   return in_center or in_bottom
 
# --- HARDWARE-SAFE VIDEO STREAMING SERVER ---
 
flask_app = Flask(__name__)
latest_frame_over = None
latest_frame_enf = None
frame_lock = threading.Lock()
 
 
def generate_frames(camera_type):
   global latest_frame_over, latest_frame_enf
   encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 65] 
   while True:
       with frame_lock:
           frame = latest_frame_over if camera_type == 'overview' else latest_frame_enf
       if frame is None:
           time.sleep(0.1)
           continue
       try:
           medium_frame = cv2.resize(frame, (480, 360))
           ret, buffer = cv2.imencode('.jpg', medium_frame, encode_param)
           if ret:
               yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
       except Exception as e:
           pass 
       time.sleep(0.2) 
 
@flask_app.route('/stream_overview')
def stream_overview(): return Response(generate_frames('overview'), mimetype='multipart/x-mixed-replace; boundary=frame')
 
@flask_app.route('/stream_enforce')
def stream_enforce(): return Response(generate_frames('enforce'), mimetype='multipart/x-mixed-replace; boundary=frame')
 
threading.Thread(target=lambda: flask_app.run(host='0.0.0.0', port=5001, debug=False, use_reloader=False), daemon=True).start()
 
print("Video Streamer running on port 5001...")
 
# --- MAIN LOOP ---
 
try:
   while True:
       ret_o, frame_overview = cam_overview.read()
       ret_e, frame_enforce = cam_enforce.read()
 
       if not ret_o or not ret_e:
           continue
 
       ai_frame_over = frame_overview.copy()
       ai_frame_enf = frame_enforce.copy()
     
       # NATIVE INFERENCE PREP
       img_o = cv2.cvtColor(frame_overview, cv2.COLOR_BGR2RGB)
       img_o = torch.from_numpy(img_o).to(device).half() / 255.0
       img_o = img_o.permute(2, 0, 1).unsqueeze(0)
 
       img_e = cv2.cvtColor(frame_enforce, cv2.COLOR_BGR2RGB)
       img_e = torch.from_numpy(img_e).to(device).half() / 255.0
       img_e = img_e.permute(2, 0, 1).unsqueeze(0)
 
       pred_over = model(img_o)
       pred_enf = model(img_e)
       pred_over = non_max_suppression(pred_over, conf_thres=0.35, iou_thres=0.45, max_det=20)[0]
       pred_enf = non_max_suppression(pred_enf, conf_thres=0.20, iou_thres=0.45, max_det=20)[0]
 
       if pred_over is not None and len(pred_over):
           df_over = pd.DataFrame(pred_over.cpu().numpy(), columns=['xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class'])
           df_over['name'] = df_over['class'].apply(lambda x: names[int(x)])
 
       else:
           df_over = pd.DataFrame(columns=['xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class', 'name'])
 
       if pred_enf is not None and len(pred_enf):
           df_enf = pd.DataFrame(pred_enf.cpu().numpy(), columns=['xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class'])
           df_enf['name'] = df_enf['class'].apply(lambda x: names[int(x)])
 
       else:
           df_enf = pd.DataFrame(columns=['xmin', 'ymin', 'xmax', 'ymax', 'confidence', 'class', 'name'])
 
       # 1. ANALYZE CAMERA 0 (Overview)
       counts = {1: 0, 2: 0, 3: 0, 4: 0}
       ambulances = {1: False, 2: False, 3: False, 4: False}
       global_crash = False
 
       for _, row in df_over.iterrows():
           box = [int(row['xmin']), int(row['ymin']), int(row['xmax']), int(row['ymax'])]
           name = str(row['name']).strip().lower() 
           conf = float(row['confidence'])  
 
           if name == 'crash':
               if is_in_roi(box, ROIS_CRASH[1]) and conf > 0.65:
                   global_crash = True
                   cv2.rectangle(ai_frame_over, (box[0], box[1]), (box[2], box[3]), (0, 0, 255), 2)
                   cv2.putText(ai_frame_over, f"UNFALL {conf:.2f}", (box[0], box[1]-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
 
           elif name in ['ambulance', 'vehicle', 'vihecle', 'car']: 
               for lane_id in [1, 2, 3, 4]:
                   if is_in_roi(box, ROIS_COUNTING[lane_id]):
                       if name == 'ambulance':
                           ambulances[lane_id] = True  
                           cv2.rectangle(ai_frame_over, (box[0], box[1]), (box[2], box[3]), (0, 255, 255), 2)
                           cv2.putText(ai_frame_over, "AMBULANZ", (box[0], box[1]-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
 
                       else:
                           counts[lane_id] += 1
                           cv2.rectangle(ai_frame_over, (box[0], box[1]), (box[2], box[3]), (0, 165, 255), 2)
                       break 
 
       crashes = {1: crash_active, 2: crash_active, 3: crash_active, 4: crash_active}
 
       # 2. DYNAMIC AI TRAFFIC LOGIC
 
       now = time.time()
       elapsed = now - last_switch
       cars_ns = counts[1] + counts[2]
       cars_ew = counts[3] + counts[4]
 
       amb_ns = ambulances[1] or ambulances[2]
       amb_ew = ambulances[3] or ambulances[4]
 
       # DIE KORRIGIERTE UNFALL- UND AMBULANZ-STEUERUNG
 
       if global_crash and not crash_active:
           print("UNFALL ERKANNT! Notfallprotokoll (8 Sek Rot) startet...")
           crash_active = True
           crash_timer = now
           if current_phase in [0, 1, 1.5]:
               update_physical_lights("Gelb", "Rot")
               time.sleep(2)
           elif current_phase in [2, 3, 3.5]:
               update_physical_lights("Rot", "Gelb")
               time.sleep(2)
 
       elif crash_active:
           time_since_crash = now - crash_timer
           if time_since_crash < 8.0:
               current_phase = 4 
           else:
               current_phase = 5
               if not global_crash:
                   print("Unfall geräumt! Kreuzung kehrt zum Normalbetrieb zurück.")
                   crash_active = False
                   current_phase = 0 
                   last_switch = now
                   blitzer_counter = {1: 0, 2: 0, 3: 0, 4: 0} 
 
       # AMBULANZ LOGIK FÜR NORD-SÜD (Spur 1 & 2)
 
       elif amb_ns:
           if current_phase == 2:      # Ost-West hat Grün -> Zwinge auf Gelb
               current_phase = 3
               last_switch = now
           elif current_phase == 3:    # Ost-West ist Gelb -> Warte 2 Sek, dann NS Rot+Gelb
               if elapsed > YELLOW_TIME:
                   current_phase = 1.5  # NEU: Rot+Gelb bevor NS Grün
                   last_switch = now
           elif current_phase == 1.5:  # NEU: NS Rot+Gelb -> dann NS Grün
               if elapsed > ROT_GELB_TIME:
                   current_phase = 0
                   last_switch = now
           elif current_phase == 1:    # NS war bereits im Gelb-Wechsel -> Zurück auf Grün
               current_phase = 0
               last_switch = now
           elif current_phase == 0:    # NS hat Grün -> Festhalten solange Krankenwagen da ist!
               last_switch = now
 
       # AMBULANZ LOGIK FÜR OST-WEST (Spur 3 & 4)
 
       elif amb_ew:
           if current_phase == 0:      # Nord-Süd hat Grün -> Zwinge auf Gelb
               current_phase = 1
               last_switch = now
           elif current_phase == 1:    # Nord-Süd ist Gelb -> Warte 2 Sek, dann EW Rot+Gelb
               if elapsed > YELLOW_TIME:
                   current_phase = 3.5  # NEU: Rot+Gelb bevor EW Grün
                   last_switch = now
           elif current_phase == 3.5:  # NEU: EW Rot+Gelb -> dann EW Grün
               if elapsed > ROT_GELB_TIME:
                   current_phase = 2
                   last_switch = now
           elif current_phase == 3:    # EW war bereits im Gelb-Wechsel -> Zurück auf Grün
               current_phase = 2
               last_switch = now
           elif current_phase == 2:    # EW hat Grün -> Festhalten solange Krankenwagen da ist!
               last_switch = now
 
       else:
           # SMART DENSITY LOGIC (Normalbetrieb)
 
           if current_phase == 0:
               if elapsed > MIN_GREEN:
                   if elapsed > MAX_WAIT and cars_ew > 0: current_phase = 1; last_switch = now
                   elif cars_ew > cars_ns: current_phase = 1; last_switch = now
                   elif cars_ew == cars_ns and elapsed > NORMAL_TIMER: current_phase = 1; last_switch = now
 
           elif current_phase == 1:
               if elapsed > YELLOW_TIME:
                   current_phase = 3.5  # NEU: nach NS-Gelb kommt EW Rot+Gelb
                   last_switch = now
 
           # NEU: EW Rot+Gelb-Phase (Vorwarnung vor EW Grün)
           elif current_phase == 3.5:
               if elapsed > ROT_GELB_TIME:
                   current_phase = 2
                   last_switch = now
 
           elif current_phase == 2:
               if elapsed > MIN_GREEN:
                   if elapsed > MAX_WAIT and cars_ns > 0: current_phase = 3; last_switch = now
                   elif cars_ns > cars_ew: current_phase = 3; last_switch = now
                   elif cars_ns == cars_ew and elapsed > NORMAL_TIMER: current_phase = 3; last_switch = now
 
           elif current_phase == 3:
               if elapsed > YELLOW_TIME:
                   current_phase = 1.5  # NEU: nach EW-Gelb kommt NS Rot+Gelb
                   last_switch = now
 
           # NEU: NS Rot+Gelb-Phase (Vorwarnung vor NS Grün)
           elif current_phase == 1.5:
               if elapsed > ROT_GELB_TIME:
                   current_phase = 0
                   last_switch = now
 
       # Convert numeric phase to Strings
       light_status = {1: "Rot", 2: "Rot", 3: "Rot", 4: "Rot"}
       if current_phase == 0:
           light_status[1] = light_status[2] = "Grün"
           blitzer_counter[1] = blitzer_counter[2] = 0
       elif current_phase == 1:
           light_status[1] = light_status[2] = "Gelb"
       elif current_phase == 1.5:                          # NEU: NS Rot+Gelb
           light_status[1] = light_status[2] = "Rot+Gelb"
       elif current_phase == 2:
           light_status[3] = light_status[4] = "Grün"
           blitzer_counter[3] = blitzer_counter[4] = 0
       elif current_phase == 3:
           light_status[3] = light_status[4] = "Gelb"
       elif current_phase == 3.5:                          # NEU: EW Rot+Gelb
           light_status[3] = light_status[4] = "Rot+Gelb"
       elif current_phase == 4:
           pass # 8-Sekunden Sperre (Alles bleibt Rot)
       elif current_phase == 5:
           # Gelb-Blinken im halbe-Sekunde-Takt
           if int(now * 2) % 2 == 0:
               light_status = {1: "Gelb", 2: "Gelb", 3: "Gelb", 4: "Gelb"}
           else:
               light_status = {1: "Aus", 2: "Aus", 3: "Aus", 4: "Aus"}
 
       # PHYSICALLY UPDATE THE LEDs
 
       update_physical_lights(ns_state=light_status[1], ew_state=light_status[3])
 
       # 3. ANALYZE CAMERA 1 (Enforcement) & BLITZER LOGIK
 
       violations = {1: False, 2: False, 3: False, 4: False}
       for _, row in df_enf.iterrows():
           box = [int(row['xmin']), int(row['ymin']), int(row['xmax']), int(row['ymax'])]
           name = str(row['name']).strip().lower()
 
           if name in ['vehicle', 'vihecle', 'car']: 
               for lane_id in [1, 2, 3, 4]:
                   if is_in_roi(box, ROIS_VIOLATION[lane_id]):
                       color = (0, 0, 255) if light_status[lane_id] == "Rot" else (0, 255, 0)
                       cv2.rectangle(ai_frame_enf, (box[0], box[1]), (box[2], box[3]), color, 2)
 
                       if light_status[lane_id] == "Rot":
                           # MAX 3 FOTOS & TÄTER-LABELING
 
                           if blitzer_counter[lane_id] < 3:
                               blitzer_counter[lane_id] += 1
                               violations[lane_id] = True
                               ts = time.strftime("%Y%m%d-%H%M%S")
 
                               filename = f"Täter{blitzer_counter[lane_id]}_lane{lane_id}_{ts}.jpg"
                               cv2.imwrite(f"violations/{filename}", ai_frame_enf)
                               print(f"BLITZER! {filename} lokal gespeichert!")
                       break
 
       # 4. SEND MQTT PAYLOADS
       for lane_id in [1, 2, 3, 4]:
           payload = {
               "lane_id": lane_id,
               "car_count": counts[lane_id],
               "light_status": "Blinkend" if current_phase == 5 else light_status[lane_id],
               "emergency": "Ja" if ambulances[lane_id] else "Nein",
               "crash": "Ja" if crashes[lane_id] else "Nein",
               "violation": "Ja" if violations[lane_id] else "Nein"
           }
           client.publish(MQTT_TOPIC, json.dumps(payload))
 
       # 5. Visual Feedback for Stream 
       for c_id, poly in ROIS_CRASH.items():
           cv2.polylines(ai_frame_over, [poly], True, (0, 255, 255), 2)
           text_x, text_y = tuple(poly[0])
           cv2.putText(ai_frame_over, "UNFALLZONE", (text_x, text_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
 
       for l_id, poly in ROIS_COUNTING.items():
           cv2.polylines(ai_frame_over, [poly], True, (0, 255, 0), 2)
           text_x, text_y = tuple(poly[0])
           cv2.putText(ai_frame_over, f"L{l_id}: {counts[l_id]}", (text_x, text_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
 
       for l_id, poly in ROIS_VIOLATION.items():
           cv2.polylines(ai_frame_enf, [poly], True, (0, 0, 255), 2)
           text_x, text_y = tuple(poly[0])
           cv2.putText(ai_frame_enf, f"L{l_id}", (text_x, text_y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
 
       with frame_lock:
           latest_frame_over = ai_frame_over.copy()
           latest_frame_enf = ai_frame_enf.copy()
 
# --- GRACEFUL SHUTDOWN UND AUTOMATISCHER SPEICHER-UPLOAD ---
except KeyboardInterrupt:
   print("\nProgramm vom Benutzer gestoppt (Ctrl+C).")
   print("Starte automatische Blitzer-Foto Synchronisation...")   
 
   if os.path.exists("violations"):
       bilder = [b for b in os.listdir("violations") if b.endswith(".jpg")]
 
       if len(bilder) > 0:
           print(f"Gefundene Täter-Fotos zum Hochladen: {len(bilder)}")
           for dateiname in bilder:
               dateipfad = os.path.join("violations", dateiname)
               try:
                   with open(dateipfad, 'rb') as f:
                       dateien = {'image': (dateiname, f, 'image/jpeg')}
                       antwort = requests.post(LAPTOP_UPLOAD_URL, files=dateien, timeout=5)
 
                   if antwort.status_code == 200:
                       os.remove(dateipfad)
                       print(f"{dateiname} an Laptop-Datenbank gesendet und lokal gelöscht.")
                   else:
                       print(f"Server-Fehler bei {dateiname}: {antwort.text}")
               except Exception as e:
                   print(f"Verbindungsfehler (Ist der Laptop/Flask an?): {e}")
                   break 
       else:
           print("Keine neuen Blitzer-Fotos vorhanden.")
 
finally:
   print("Shutting down... Turning off physical lights.")
   GPIO.output(ALL_PINS, GPIO.LOW)
   GPIO.cleanup()
   client.loop_stop()
   cam_overview.release()
   cam_enforce.release()
   cv2.destroyAllWindows()
