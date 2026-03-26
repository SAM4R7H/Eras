import requests
import time
import random
import sys

API_URL = "http://127.0.0.1:8000/incidents/"

# Incident types
INCIDENT_TYPES = ["Medical", "Fire", "Traffic Collision", "Rescue", "Hazmat"]

# Jaipur Boundary Coordinates
LAT_MIN, LAT_MAX = 26.8700, 26.9500
LNG_MIN, LNG_MAX = 75.7400, 75.8600

def generate_random_incident():
    """Generates a fake 911 call and sends it to the FastAPI backend"""
    inc_type = random.choice(INCIDENT_TYPES)
    lat = round(random.uniform(LAT_MIN, LAT_MAX), 4)
    lng = round(random.uniform(LNG_MIN, LNG_MAX), 4)
    
    try:
        response = requests.post(API_URL, params={"incident_type": inc_type, "lat": lat, "lng": lng})
        if response.status_code == 200:
            inc_id = response.json()['incident']['incident_id']
            print(f"🚨 [NEW CALL] {inc_id} | Type: {inc_type}")
    except requests.exceptions.ConnectionError:
        print("❌ Cannot connect to backend. Is the server running?")

if __name__ == "__main__":
    print("\n================================================")
    print("      🚨 ERAS INCIDENT SIMULATOR 🚨      ")
    print("================================================")
    print("Select Demo Intensity Mode:")
    print("  1. Low Demand      (1 call every 15-25 sec)")
    print("  2. Moderate Demand (1 call every 8-12 sec)")
    print("  3. CRISIS MODE     (1 call every 2-4 sec) 🔥")
    print("================================================")
    
    # Get user input from the terminal
    mode = input("Enter mode number (1, 2, or 3): ").strip()
    
    if mode == '1':
        min_t, max_t = 15, 25
        mode_name = "LOW DEMAND"
    elif mode == '3':
        min_t, max_t = 2, 4
        mode_name = "CRISIS MODE"
    else:
        min_t, max_t = 8, 12 # Default to moderate
        mode_name = "MODERATE DEMAND"

    print(f"\n✅ Starting Simulator in {mode_name}. Press Ctrl+C to stop.\n")
    
    # Infinite loop to keep generating calls based on chosen speed
    while True:
        generate_random_incident()
        
        # NOTE: We removed the auto-resolve function! 
        # You must now manually click the buttons on the React UI to free up trucks!
        
        sleep_time = random.randint(min_t, max_t)
        time.sleep(sleep_time)