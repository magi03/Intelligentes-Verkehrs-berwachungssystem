import os
import sqlite3
import json
import paho.mqtt.client as mqtt
from flask import Flask, render_template, jsonify, request
import threading
from datetime import datetime

app = Flask(__name__)

# --- ORDNER FÜR BLITZER-FOTOS SICHERSTELLEN ---
os.makedirs("static/violations", exist_ok=True)

def get_db_connection():
    conn = sqlite3.connect('smart_traffic.db', timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # 1. Kreuzungen
    c.execute('CREATE TABLE IF NOT EXISTS Intersections (id INTEGER PRIMARY KEY, name TEXT, location TEXT)')
    
    # 2. Fahrstreifen
    c.execute('CREATE TABLE IF NOT EXISTS Lanes (id INTEGER PRIMARY KEY, intersection_id INTEGER, lane_name TEXT, FOREIGN KEY(intersection_id) REFERENCES Intersections(id))')
    
    # 3. Verkehrsprotokolle
    c.execute('''CREATE TABLE IF NOT EXISTS Traffic_Logs 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, lane_id INTEGER, car_count INTEGER, 
                  light_status TEXT, emergency_active INTEGER, crash_detected INTEGER, 
                  violation_detected INTEGER, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(lane_id) REFERENCES Lanes(id))''')
                  
    # 4. NEU: Blitzer-Akten (Speichert die Pfade zu den echten Bildern)
    c.execute('''CREATE TABLE IF NOT EXISTS Violations 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, lane_id INTEGER, 
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, image_path TEXT, 
                  FOREIGN KEY(lane_id) REFERENCES Lanes(id))''')
    
    # Standard-Werte eintragen
    c.execute("INSERT OR IGNORE INTO Intersections (id, name, location) VALUES (1, 'Main Intersection', 'Center')")
    for i, name in enumerate(['Nord', 'Süd', 'Ost', 'West'], 1):
        c.execute("INSERT OR IGNORE INTO Lanes (id, intersection_id, lane_name) VALUES (?, 1, ?)", (i, name))
        
    conn.commit()
    conn.close()

init_db()

# --- MQTT SETUP ---
def on_message(client, userdata, message):
    try:
        data = json.loads(message.payload.decode("utf-8"))
        is_em = 1 if data.get('emergency') == "Ja" else 0
        is_cr = 1 if data.get('crash') == "Ja" else 0
        is_vi = 1 if data.get('violation') == "Ja" else 0
        
        local_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""INSERT INTO Traffic_Logs 
                     (lane_id, car_count, light_status, emergency_active, crash_detected, violation_detected) 
                     VALUES (?, ?, ?, ?, ?, ?)""",
                  (data['lane_id'], data['car_count'], data['light_status'], is_em, is_cr, is_vi))
        conn.commit()
        conn.close()
        print(f" DB Update | L{data['lane_id']} | Cars: {data['car_count']} | Light: {data['light_status']} | Violation: {data.get('violation')}")
    except Exception as e:
        print(f" DB Error: {e}")

JETSON_IP = "172.20.10.5" 

mqtt_sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "Flask_Backend", protocol=mqtt.MQTTv311)
mqtt_sub.on_message = on_message
try:
    mqtt_sub.connect(JETSON_IP, 1883)
    mqtt_sub.subscribe("traffic/intersection1/data")
    mqtt_sub.loop_start()
except Exception as e:
    print(f" MQTT Failed: {e}")

# --- WEB DASHBOARD ROUTES ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/history')
def history():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT Traffic_Logs.*, Lanes.lane_name 
        FROM Traffic_Logs 
        JOIN Lanes ON Traffic_Logs.lane_id = Lanes.id 
        ORDER BY timestamp DESC LIMIT 70
    ''')
    logs = c.fetchall()
    conn.close()
    return render_template('history.html', logs=logs)

# --- API ENDPOINTS (DATA & UPLOADS) ---
@app.route('/api/data')
def api_data():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        lanes_data = {}
        for i in range(1, 5):
            c.execute("SELECT * FROM Traffic_Logs WHERE lane_id = ? ORDER BY timestamp DESC LIMIT 1", (i,))
            row = c.fetchone()
            if row:
                lanes_data[f"lane_{i}"] = {
                    "count": row['car_count'],
                    "status": row['light_status'],
                    "emergency": "Ja" if row['emergency_active'] else "Nein",
                    "crash": "Ja" if row['crash_detected'] else "Nein",
                    "violation": "Ja" if row['violation_detected'] else "Nein"
                }
            else:
                lanes_data[f"lane_{i}"] = {"count": 0, "status": "Rot", "emergency": "Nein", "crash": "Nein", "violation": "Nein"}
        conn.close()
        return jsonify({"lanes": lanes_data})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/stats')
def get_stats():
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT 
                strftime('%H:%M', timestamp) as time_min, 
                SUM(CASE WHEN lane_id IN (1, 2) THEN car_count ELSE 0 END) as ns_cars,
                SUM(CASE WHEN lane_id IN (3, 4) THEN car_count ELSE 0 END) as ew_cars
            FROM Traffic_Logs 
            GROUP BY time_min 
            ORDER BY time_min DESC 
            LIMIT 15
        """)
        rows = c.fetchall()
        conn.close()
        return jsonify([dict(row) for row in reversed(rows)])
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/api/upload_violation', methods=['POST'])
def upload_violation():
    try:
        if 'image' not in request.files:
            return jsonify({"error": "Kein Bild gesendet"}), 400
            
        file = request.files['image']
        filename = file.filename

        filepath = os.path.join("static/violations", filename)
        file.save(filepath)
        
        lane_id = 1
        if "_lane" in filename:
            try:
                lane_id = int(filename.split("_lane")[1].split("_")[0])
            except:
                pass

        try:
            # Holt sich den hinteren Teil "20260623-232146"
            time_string = filename.split('_')[-1].replace('.jpg', '')
            # Wandelt es in ein echtes Datum-Objekt um
            dt = datetime.strptime(time_string, "%Y%m%d-%H%M%S")
            # Formatiert es für die Datenbank lesbar (YYYY-MM-DD HH:MM:SS)
            jetson_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            # Fallback, falls der Dateiname mal anders heißt: Lokale Laptop-Zeit nehmen
            jetson_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db_path = f"/static/violations/{filename}"
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO Violations (lane_id, image_path, timestamp) VALUES (?, ?, ?)", 
                  (lane_id, db_path, jetson_time))
        conn.commit()
        conn.close()
        
        print(f" Beweisfoto empfangen & Akte angelegt: Spur {lane_id} | Datei: {filename}")
        return jsonify({"status": "Erfolg"}), 200
        
    except Exception as e:
        print(f" Fehler beim Speichern des Fotos: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/violations')
def violations_gallery():
    conn = get_db_connection()
    c = conn.cursor()
    
    c.execute('''
        SELECT Violations.*, Lanes.lane_name 
        FROM Violations 
        JOIN Lanes ON Violations.lane_id = Lanes.id 
        ORDER BY timestamp DESC
    ''')
    photos = c.fetchall()
    conn.close()
    return render_template('violations.html', photos=photos)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)