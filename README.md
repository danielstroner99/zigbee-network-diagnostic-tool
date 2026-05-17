# Zigbee Network Diagnostic Tool

Real-time Zigbee network monitoring web application built with Python and Flask. Displays live link quality (LQI) data from all Zigbee devices on an interactive building floor plan, with network topology visualisation and historical LQI tracking.

Developed as part of the Batch Data Processing course at Slovak University of Technology in Bratislava (2025).

## Features

- Live LQI monitoring per device with 10-second update interval
- Interactive building floor plan with device status overlay
- Network topology visualisation (Zigbee2MQTT mesh map)
- Rolling 1-hour LQI history buffer per device (360 records)
- WebSocket-based real-time frontend updates
- REST API endpoints for topology refresh and retrieval
- MQTT broker access over VPN for distributed team development

## Tech Stack

- **Backend:** Python 3, Flask, Flask-SocketIO
- **MQTT:** paho-mqtt, Zigbee2MQTT
- **Frontend:** HTML, JavaScript, WebSocket
- **Infrastructure:** MQTT broker (Mosquitto), Tailscale VPN

## Project Structure

```
├── app.py               # Flask backend — MQTT client, WebSocket server, REST API
├── sniffer_parser.py    # Experimental Zigbee packet sniffer (pyshark/tshark, not active)
├── templates/
│   └── index.html       # Frontend — floor plan map with live device overlay
├── map.html             # Static floor plan reference
└── lqi_history.json     # Runtime LQI history (auto-generated, not tracked in git)
```

## Setup

### Requirements

```
pip install flask flask-socketio paho-mqtt
```

### Configuration

Before running, update the following in `app.py`:

- `mqtt_client.username_pw_set("YOUR_MQTT_USER", "YOUR_MQTT_PASSWORD")` — set your MQTT broker credentials
- `mqtt_client.connect("YOUR_BROKER_IP", 1883)` — set your MQTT broker IP address
- `places` dictionary — map your device friendly names to room numbers

### Run

```bash
python app.py
```

The web interface is available at `http://localhost:5000`.

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Web interface |
| GET | `/api/topology` | Return last captured network topology |
| POST | `/api/topology/refresh` | Request fresh device list and network map from Zigbee2MQTT |
