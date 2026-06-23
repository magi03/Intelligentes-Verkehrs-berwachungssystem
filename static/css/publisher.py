import paho.mqtt.client as mqtt
import json
import time
import random

BROKER = "172.20.10.5" 
TOPIC = "traffic/intersection1/data"

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, "Jetson_Nano_Simulator")
client.connect(BROKER)

print(" Starte AI Traffic Simulation für alle 4 Spuren...")

while True:
    for lane in [1, 2, 3, 4]:
        payload = {
            "lane_id": lane,
            "car_count": random.randint(0, 15),
            "light_status": random.choice(["Grün", "Gelb", "Rot"]),
            "emergency": "Ja" if random.random() < 0.05 else "Nein",
            "crash": "Nein",     
            "violation": "Nein" 
        }
        client.publish(TOPIC, json.dumps(payload))
        print(f"Sent data for Lane {lane}: {payload}")
    
    time.sleep(2)