# 0. Needed libraries
from time import timezone                # Time offset calculations relative to UTC
import paho.mqtt.client as mqtt          # Communication with the MQTT broker (Zigbee devices)
from flask import Flask, render_template # Core framework to host the web server and serve your HTML map file
from flask import request                # Handles HTTP requests - POST or GET methods
from flask_socketio import SocketIO      # Web sockets - real-time communication between server and web clients
import json                              # JSON data handling
import time                              # Time-related functions - sleep, system time
import threading                         # Threading for concurrent tasks - multitasking
from datetime import datetime            # Date and time handling


# 1. Initial setup:

# 1.a Flask app + SocketIO 
app = Flask(__name__)                    # Creates basic web application
socketio = SocketIO(app)                 # Adds communication capabilities (WebSocket) to the app
                                         # The server can emit events to connected web clients in real time

# 1.b Dictionary of device names to room numbers - 6999 meaning unknown room
places = {
    "0xa4c1384ac83c5e41": 6999,
    "NB 661 SLZB-06 Repeater": 661,
    "NB 665 (Oravec) SLZB-06M Repeater": 665,
    "NB 666 (Kitchen) SLZB-06": 666,
    "Plug NB 632 Students": 632,
    "Plug NB 633": 633,
    "Plug NB 635": 635,
    "Plug NB 636": 636,
    "Plug NB 637": 637,
    "Plug NB 641": 641,
    "Plug NB 660 Boiler": 660,
    "Plug NB 661": 661,
    "Plug NB 662": 662,
    "Plug NB 663 Fridge": 663,
    "Plug NB 664": 664,
    "Plug NB 665": 665,
    "Plug NB 666": 666,
    "Plug NB C 671": 671,
    "Plug NB C 671 tefal": 671,
    "Plug NB C 673": 673,
    "Plug NB C 674": 674,
    "Plug NB C 675": 675,
    "Plug NB C 676": 676,
    "Plug NB C 677": 677,
    "Pohybový senzor NB C 675 Ikea": 675,
    "Pohybový senzor NB C 677": 677,
    "Presence NB B 634": 634,
    "Presence NB C 677": 677,
    "Radiator NB C 673": 673,
    "Radiator NB C 674": 674,
    "Radiator NB C 675": 675,
    "Radiator NB C 676": 676,
    "Radiator NB C 677": 677,
    "Roller Shade Driver 1": 6999,
    "Roller Shade Driver 2": 6999,
    "Table Light NB C 677": 677,
    "Teplomer&Vlhkomer NB C 675": 675,
    "Window sensor NB C 674": 674
}

# 1.c Timer
LAST_SENT_PER_DEVICE = {}
SEND_INTERVAL = 10                       # 10 seconds

# 1.d Z2M REQUEST/RESPONSE topics ---
DEVICES_REQUEST_TOPIC = "zigbee2mqtt/bridge/request/devices"
DEVICES_RESPONSE_TOPIC = "zigbee2mqtt/bridge/response/devices"

NETWORKMAP_REQUEST_TOPIC = "zigbee2mqtt/bridge/request/networkmap"
NETWORKMAP_RESPONSE_TOPIC = "zigbee2mqtt/bridge/response/networkmap"

# 1.e Device mapping - multithreads
devices_lock = threading.Lock()           # protects/locks access to variable from 2 threads
devices_event = threading.Event()         # a “signal” that the requested data arrived

IEEE_TO_FRIENDLY = {}                     # "0xabc..." -> "Plug NB 666"
LAST_DEVICES_TS = None                    # timestamp for when mapping was updated

# 1.f Network map  - multithreads
networkmap_lock = threading.Lock()        # protects/locks access to variable from 2 threads
networkmap_event = threading.Event()      # a “signal” that the requested data arrived

LAST_NETWORKMAP_LINKS = None              # already "cleaned" links
LAST_NETWORKMAP_TS = None                 # timestamp for when networkmap was updated

# 1.g LQI history (stores a rolling window of LQI samples per device in memory)
LQI_HISTORY = {}                          # device_name -> list of records
HISTORY_FILE = "lqi_history.json"
history_lock = threading.Lock()
MAX_RECORDS_PER_DEVICE = 360              # 1 hour with 10 s interval (60 min * 6)


# 2. MQTT Logic
def on_message(client, userdata, message):
    try:
        # Adress & decoding
        tema = message.topic              
        payload = message.payload.decode("utf-8")
        
        # DEBUG: all messages
        print(f"[MQTT] Topic: {tema} | Payload length: {len(payload)} | Preview: {payload[:100]}", flush=True)

        # DEVICES RESPONSE (IEEE -> friendly_name)
        if tema == DEVICES_RESPONSE_TOPIC:
            print(f"[DEVICES_RESPONSE] ✅ RECEIVED on topic: {tema}", flush=True)
            
            # Changes text to Python object and extracts data
            msg_json = json.loads(payload)    
            mapping = extract_ieee_to_friendly(msg_json)

            #Update with lock:
            with devices_lock:
                global IEEE_TO_FRIENDLY, LAST_DEVICES_TS
                IEEE_TO_FRIENDLY = mapping  # Update the shared dictionary atomically without anyone reading it mid-update
                LAST_DEVICES_TS = datetime.now().isoformat()

            #Unlock:
            devices_event.set()
            print(f"DEVICES received: {len(mapping)} items")
            return

        # NETWORKMAP RESPONSE
        if tema == NETWORKMAP_RESPONSE_TOPIC:
            print(f"[NETWORKMAP_RESPONSE] ✅ RECEIVED on topic: {tema}", flush=True)
            
            # Changes text to Python object and extracts data
            msg_json = json.loads(payload)
            with devices_lock:
                local_map = dict(IEEE_TO_FRIENDLY)
            links_clean = extract_links_from_networkmap(msg_json, local_map)

            #Update with lock:
            with networkmap_lock:
                global LAST_NETWORKMAP_LINKS, LAST_NETWORKMAP_TS
                LAST_NETWORKMAP_LINKS = links_clean
                LAST_NETWORKMAP_TS = datetime.now().isoformat()

            #Unlock:
            networkmap_event.set()  # to wake up any waiting requests
            print(f"NETWORKMAP received: {len(links_clean)} links")
            return

        # DEVICES TELEMETRY (zigbee2mqtt/+)
        data = json.loads(payload)
        # print(f"Payload: {payload}")
        # print(f"Message: {message}")
        device_name = tema.split("/", 1)[1] if "/" in tema else tema

        # TIME FILTER (10s update interval)
        now = datetime.now()
        last = LAST_SENT_PER_DEVICE.get(device_name) # When last message about device was sent to web

        if last is not None:
            if (now - last).total_seconds() < SEND_INTERVAL:
                return

        LAST_SENT_PER_DEVICE[device_name] = now

        miestnost = places.get(device_name, 6999)

        #LQI extraction
        lq = data.get("linkquality", 0)
        cas = datetime.now().strftime("%H:%M:%S")

        # SAVING TO HISTORY (memory)
        record = {
            "cas": cas,
            "miestnost": miestnost,
            "lqi": int(lq)
        }

        with history_lock:
            if device_name not in LQI_HISTORY:
                LQI_HISTORY[device_name] = []
            LQI_HISTORY[device_name].append(record)

            # 1 HOUR LIMIT (360 records - 10s)
            if len(LQI_HISTORY[device_name]) > MAX_RECORDS_PER_DEVICE:
                LQI_HISTORY[device_name] = LQI_HISTORY[device_name][-MAX_RECORDS_PER_DEVICE:]

        # Websocket emit
        socketio.emit('nova_sprava', {
            'cas': cas,
            'zariadenie': device_name,
            'miestnost': miestnost,
            'kvalita': lq
        })
        #Console message:
        print(f"Odoslané: {device_name} (Miestnosť: {miestnost})")

    except Exception as e:
        print(f"Chyba v on_message: {e}")


# 3. Save history & handle everything

# 3.a PERIODIC SAVE TO FILE - 10 seconds
def save_history_periodically():
    while True:
        time.sleep(10)
        with history_lock:
            snapshot = dict(LQI_HISTORY)  # fast copy

        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False, indent=2)
            print("History saved to JSON.")
        except Exception as e:
            print(f"Chyba pri ukladani JSON: {e}")

# 3b. HANDLE CONNECT (client connection)
@socketio.on('connect')
def handle_connect():
    print(f"Klient sa pripojil: {request.sid}")

# 3c. HANDLE GET_HISTORY (client requests history)
@socketio.on('get_history')
def handle_get_history():
    with history_lock:
        socketio.emit('history_data', LQI_HISTORY, room=request.sid)
    print(f"História odoslaná pre: {request.sid}")

# 3d. HANDLE REQUEST_ALL_DEVICES (client requests to refresh the topology - DEPRECATED)
# Use HTTP POST instead on /api/topology/refresh!
@socketio.on('request_all_devices')
def handle_request_all_devices():
    print("Varovanie: WebSocket handler sa už nepoužíva. Používaj HTTP POST na /api/topology/refresh")
    socketio.emit('request_result', {
        "success": False,
        "error": "Prosím použite frontend tlačidlo, ktoré volá /api/topology/refresh"
    }, room=request.sid)


# 4. IEEE --> friendly_name
def extract_ieee_to_friendly(devices_msg_json: dict) -> dict:
    
   # Waiting for Zigbee2MQTT response for devices.
   # Usually looks like this: {"data": {"value": [ {...}, {...} ]}}
   
    print(f"[EXTRACT DEVICES] Raw JSON keys: {list(devices_msg_json.keys())}", flush=True)
    print(f"[EXTRACT DEVICES] Full JSON preview: {str(devices_msg_json)[:500]}", flush=True)
    
    value = devices_msg_json.get("data", {}).get("value", [])
    
    print(f"[EXTRACT DEVICES] Extracted value type: {type(value)}, length: {len(value) if isinstance(value, list) else 'N/A'}", flush=True)
    
    mapping = {}

    if isinstance(value, list):
        for d in value:
            if not isinstance(d, dict):
                continue
            # Because different systems, libraries, and Zigbee2MQTT versions 
            # use different key names for the same field.
            ieee = d.get("ieee_address") or d.get("ieeeAddr") or d.get("ieee")
            friendly = d.get("friendly_name") or d.get("friendlyName") or d.get("name")
            if ieee and friendly:
                mapping[str(ieee)] = str(friendly)
    else:
        print(f"[EXTRACT DEVICES] Value is not a list! Type: {type(value)}", flush=True)

    print(f"[EXTRACT DEVICES] Mapping created: {len(mapping)} devices", flush=True)
    return mapping


# 5. Mapping logic

def extract_links_from_networkmap(msg_json: dict, ieee_to_name: dict) -> list[dict]:
   
    # Pulls links from networkmap a adds friendly names if known.
    # Waits for: {"data": {"value": {"links": [...]}}}
    
    print(f"[EXTRACT NETWORKMAP] Raw JSON keys: {list(msg_json.keys())}", flush=True)
    print(f"[EXTRACT NETWORKMAP] Full JSON preview: {str(msg_json)[:500]}", flush=True)
    
    value = msg_json.get("data", {}).get("value", {})
    
    if not isinstance(value, dict):
        print(f"[EXTRACT NETWORKMAP] Value is not a dict! Type: {type(value)}", flush=True)
    
    links = value.get("links", [])
    
    print(f"[EXTRACT NETWORKMAP] Found {len(links)} links", flush=True)
    
    cleaned = []

    for link in links:
        source = link.get("source", {}) or {}
        target = link.get("target", {}) or {}

        source_ieee = link.get("sourceIeeeAddr") or source.get("ieeeAddr")
        target_ieee = link.get("targetIeeeAddr") or target.get("ieeeAddr")

        cleaned.append({
            "source_ieee": source_ieee,
            "source_name": ieee_to_name.get(str(source_ieee), None),
            "source_nwk":  link.get("sourceNwkAddr") or source.get("networkAddress"),

            "target_ieee": target_ieee,
            "target_name": ieee_to_name.get(str(target_ieee), None),
            "target_nwk":  target.get("networkAddress"),

            "lqi":          link.get("lqi", link.get("linkquality")),
            "depth":        link.get("depth"),
            "relationship": link.get("relationship"),
            "deviceType":   link.get("deviceType"),
        })

    print(f"[EXTRACT NETWORKMAP] Cleaned {len(cleaned)} links", flush=True)
    return cleaned

# 6. Sending requests for mapping

def request_devices(timeout_s: int = 300) -> dict:   # IEEE -> friendly_name
    devices_event.clear()
    print(f"[REQUEST_DEVICES] Publishing request to {DEVICES_REQUEST_TOPIC}", flush=True)
    mqtt_client.publish(DEVICES_REQUEST_TOPIC, json.dumps({}))

    ok = devices_event.wait(timeout=timeout_s)       # wait for response
    if not ok:
        print(f"[REQUEST_DEVICES] TIMEOUT after {timeout_s}s! Returning cached devices...", flush=True)
        with devices_lock:
            if IEEE_TO_FRIENDLY:
                print(f"[REQUEST_DEVICES] Returning {len(IEEE_TO_FRIENDLY)} cached devices", flush=True)
                return dict(IEEE_TO_FRIENDLY)
        raise TimeoutError("DEVICES timeout: response not received after " + str(timeout_s) + "s (no cached data)")

    with devices_lock:
        return dict(IEEE_TO_FRIENDLY)
    # Make a safe snapshot copy while no one is modifying it

# ==========================================================================
# ==========================================================================
'''
Why do we use locks:
MQTT thread (on_message) updates IEEE_TO_FRIENDLY when devices response arrives.
HTTP/Flask thread (/api/topology or /api/topology/refresh) reads IEEE_TO_FRIENDLY.
Without a lock, the HTTP thread could read it while it’s half-updated, giving inconsistent data or rare weird bugs.
'''  
# ==========================================================================
# ==========================================================================

def request_networkmap(timeout_s: int = 300) -> list[dict]:
    networkmap_event.clear()
    print(f"[REQUEST_NETWORKMAP] Publishing request to {NETWORKMAP_REQUEST_TOPIC}", flush=True)
    mqtt_client.publish(NETWORKMAP_REQUEST_TOPIC, json.dumps({"type": "raw"}))

    ok = networkmap_event.wait(timeout=timeout_s)
    # Pause this function and wait until another thread signals that 
    # the network map has arrived — or until time runs out
        # true => event was set before timeout (success)
    if not ok:
        print(f"[REQUEST_NETWORKMAP] TIMEOUT after {timeout_s}s! Returning cached networkmap...", flush=True)
        with networkmap_lock:
            if LAST_NETWORKMAP_LINKS:
                print(f"[REQUEST_NETWORKMAP] Returning {len(LAST_NETWORKMAP_LINKS)} cached links", flush=True)
                return list(LAST_NETWORKMAP_LINKS)
        raise TimeoutError("NETWORKMAP timeout: response not received after " + str(timeout_s) + "s (no cached data)")

    with networkmap_lock:
        return list(LAST_NETWORKMAP_LINKS or [])

'''
Threads can:
    .set() → turn ON
    .clear() → turn OFF
    .wait() → sleep until ON
'''
# 7. MQTT Client Setup

#mqtt_client = mqtt.Client() - original command, not working for everyone
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1) #<- works for all of us
mqtt_client.username_pw_set("YOUR_MQTT_USER", "YOUR_MQTT_PASSWORD")
mqtt_client.on_message = on_message
mqtt_client.connect("YOUR_BROKER_IP", 1883)
mqtt_client.subscribe("zigbee2mqtt/+")
mqtt_client.subscribe(DEVICES_RESPONSE_TOPIC)
mqtt_client.subscribe(NETWORKMAP_RESPONSE_TOPIC)

# loop_start() is important - it says to Python: 
# "Listen to MQTT in the background, but don't stop here, continue with the rest of the code"
mqtt_client.loop_start()


# 8. Web routing
# Backend route, which button request later
@app.route("/api/topology/refresh", methods=["POST"])
def api_topology_refresh():
    try:
        print("[DEBUG] /api/topology/refresh: Requesting devices (max 5 min)...", flush=True)
        mapping = request_devices(timeout_s=300)      # IEEE -> friendly_name (5 minutes)
        
        print(f"[DEBUG] /api/topology/refresh: Devices received: {len(mapping)} items. Requesting networkmap (max 5 min)...", flush=True)
        links = request_networkmap(timeout_s=300)    # already with names (5 minutes)

        print(f"[DEBUG] /api/topology/refresh: Success! devices={len(mapping)}, links={len(links)}", flush=True)
        return {
            "devices_timestamp": LAST_DEVICES_TS,
            "networkmap_timestamp": LAST_NETWORKMAP_TS,
            "devices_count": len(mapping),
            "links_count": len(links),
            "links": links
        }, 200

    except TimeoutError as e:
        print(f"[DEBUG] /api/topology/refresh: TimeoutError - {e}", flush=True)
        return {"error": f"Timeout: {str(e)}"}, 504
    except Exception as e:
        print(f"[DEBUG] /api/topology/refresh: Exception - {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return {"error": str(e)}, 500
# other than timeout error:
    # JSON parsing crash
    # KeyError
    # AttributeError
    # Bug in your code
    # Library crash

# endpoint, that gives last saved map (without site refresh):
@app.route("/api/topology", methods=["GET"])
def api_topology_get():
    with devices_lock:
        mapping_count = len(IEEE_TO_FRIENDLY)

    with networkmap_lock:
        if LAST_NETWORKMAP_LINKS is None:
            return {"error": "No topology captured yet"}, 404
        links = LAST_NETWORKMAP_LINKS

    return {
        "devices_timestamp": LAST_DEVICES_TS,
        "networkmap_timestamp": LAST_NETWORKMAP_TS,
        "devices_count": mapping_count,
        "links_count": len(links),
        "links": links
    }, 200

@app.route('/')
def index():
    # Rendering HTML page
    return render_template('index.html')

if __name__ == '__main__':

    socketio.start_background_task(save_history_periodically)
    # Web server starts at port 5000, accessible from any device in the network
    socketio.run(app, host='0.0.0.0', port=5000)