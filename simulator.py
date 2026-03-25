
import requests
import time
import random

API_URL = "http://127.0.0.1:8000/incidents/"
RESOLVE_URL = "http://127.0.0.1:8000/incidents/{}/resolve"

INCIDENT_TYPES = ["Medical", "Fire", "Traffic Collision", "Rescue", "Hazmat"]
LAT_MIN, LAT_MAX = 26.8700, 26.9500
LNG_MIN, LNG_MAX = 75.7400, 75.8600

active_incident_ids = []

def generate_random_incident():
    inc_type = random.choice(INCIDENT_TYPES)
    lat = round(random.uniform(LAT_MIN, LAT_MAX), 4)
    lng = round(random.uniform(LNG_MIN, LNG_MAX), 4)
    try:
        response = requests.post(API_URL, params={"incident_type": inc_type, "lat": lat, "lng": lng})
        if response.status_code == 200:
            inc_id = response.json()['incident']['incident_id']
            active_incident_ids.append(inc_id)
            print(f"🚨 [NEW CALL] {inc_id} | Type: {inc_type}")
    except requests.exceptions.ConnectionError:
        pass

def resolve_random_incident():
    if not active_incident_ids: return
    inc_id = random.choice(active_incident_ids)
    try:
        response = requests.post(RESOLVE_URL.format(inc_id))
        if response.status_code == 200:
            active_incident_ids.remove(inc_id)
            print(f"✅ [RESOLVED] {inc_id}")
    except requests.exceptions.ConnectionError:
        pass

if __name__ == "__main__":
    print("Starting CAD Simulator...")
    while True:
        generate_random_incident()
        if random.random() > 0.4:
            resolve_random_incident()
        time.sleep(random.randint(5, 15))
