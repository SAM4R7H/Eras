
import random
from datetime import datetime

def evaluate_incident(incident_type: str, reported_time: datetime):
    if incident_type == "Fire":
        priority = random.randint(75, 100)
        required_units = {"Engine": 2, "Ladder": 1}
    elif incident_type == "Medical":
        priority = random.randint(40, 95)
        if priority > 80:
            required_units = {"Ambulance": 1, "Engine": 1}
        else:
            required_units = {"Ambulance": 1}
    elif incident_type == "Traffic Collision":
        priority = random.randint(60, 90)
        required_units = {"Engine": 1, "Ambulance": 1}
    elif incident_type == "Hazmat":
        priority = random.randint(90, 100) 
        required_units = {"Engine": 2, "Ladder": 1}
    elif incident_type == "Rescue":
        priority = random.randint(70, 95)
        required_units = {"Ladder": 1, "Ambulance": 1}
    else:
        priority = 50
        required_units = {"Engine": 1}

    # Rush hour modifier
    hour = reported_time.hour
    if hour in [8, 9, 17, 18]:
        priority = min(100, priority + 5)

    return priority, required_units
