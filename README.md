# 🚦 Intelligentes Verkehrsüberwachungssystem (Smart Traffic Intersection)

Dieses Repository enthält den Quellcode für meine Bachelorarbeit. Es handelt sich um ein KI-gestütztes System zur intelligenten Verkehrsüberwachung und dynamischen Ampelsteuerung, optimiert für Edge-Computing.

## 🌟 Hauptfunktionen

* **Echtzeit-Verkehrsanalyse:** Erkennung von Fahrzeugen zur dynamischen Anpassung der Ampelphasen.
* **Priorisierung von Rettungskräften:** Automatische Erkennung von Krankenwagen (Ambulances) zur sofortigen Freischaltung der Fahrspur.
* **Unfallerkennung:** Identifikation von Unfällen im Kreuzungsbereich zur schnellen Alarmierung.
* **Rotlicht-Überwachung:** Erkennung von Verkehrsverstößen inklusive automatischer Beweissicherung.
* **Web-Dashboard:** Eine integrierte Flask-Anwendung zur Live-Überwachung und Historien-Auswertung.

## 🛠️ Technologie-Stack

* **Hardware:** Nvidia Jetson Nano
* **KI-Modell:** YOLOv5 (Custom Trained)
* **Backend:** Python, Flask, OpenCV
* **Kommunikation:** MQTT (für die IoT-Steuerung)
* **Frontend:** HTML, CSS

## 📂 Projektstruktur

* `app.py`: Das Flask-Backend und der Webserver für das Dashboard.
* `steuerungslogik.py`: Die Hauptlogik für die dynamische Ampelschaltung basierend auf den KI-Daten.
* `Daten_Sammlung.py`: Skript zur Erfassung und Verarbeitung der Videoströme und Sensorik.
* `ROI_calib.py`: Werkzeug zur Kalibrierung der "Regions of Interest" (z.B. Haltelinien, Kreuzungsmitte).
* `templates/` & `static/`: Frontend-Dateien für das Web-Dashboard.

## ⚙️ Wichtiger Hinweis zur Ausführung
*Aufgrund der Dateigrößenbeschränkungen von GitHub sind die trainierten YOLOv5-Gewichte (`best.pt`) sowie die lokalen Datenbanken und Blitzerfotos in diesem Repository nicht enthalten. Diese werden für die vollständige Ausführung lokal benötigt.*