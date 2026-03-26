from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from models import Incident, Unit, Station, Location
import uuid
from datetime import datetime
from ai_engine import evaluate_incident
from optimizer import optimize_dispatch, _get_route
import json

app = FastAPI(title="AI CAD System API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for c in dead:
            self.disconnect(c)


manager = ConnectionManager()

active_incidents: dict[str, Incident] = {}
fleet_units:      dict[str, Unit]     = {}
fire_stations:    dict[str, Station]  = {}
# Store calc steps per incident for the AI panel
calc_log:         dict[str, list]     = {}
audit_log_db:     list[dict]          = []  # <--- NEW: Permanent history


def seed_data():
    fire_stations["ST-1"] = Station(
        station_id="ST-1", name="Civil Lines Station",
        location=Location(lat=26.9260, lng=75.8235),
        units=["ENG-1", "MED-1"],
    )
    fleet_units["ENG-1"] = Unit(unit_id="ENG-1", type="Engine", station_id="ST-1", capability="Heavy Rescue")
    fleet_units["MED-1"] = Unit(unit_id="MED-1", type="Ambulance", station_id="ST-1", capability="ALS")

    fire_stations["ST-2"] = Station(
        station_id="ST-2", name="Vaishali Nagar Station",
        location=Location(lat=26.9124, lng=75.7373),
        units=["ENG-2", "LAD-1"],
    )
    fleet_units["ENG-2"] = Unit(unit_id="ENG-2", type="Engine", station_id="ST-2", capability="Standard")
    fleet_units["LAD-1"] = Unit(unit_id="LAD-1", type="Ladder", station_id="ST-2", capability="Standard")

    fire_stations["ST-3"] = Station(
        station_id="ST-3", name="Mansarovar Station",
        location=Location(lat=26.8535, lng=75.7726),
        units=["MED-2", "ENG-3"],
    )
    fleet_units["MED-2"] = Unit(unit_id="MED-2", type="Ambulance", station_id="ST-3", capability="BLS")
    fleet_units["ENG-3"] = Unit(unit_id="ENG-3", type="Engine", station_id="ST-3", capability="Standard")

seed_data()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/system-status")
def get_system_status():
    return {
        "active_incidents": [i.model_dump() for i in active_incidents.values()],
        "units":            [u.model_dump() for u in fleet_units.values()],
        "stations":         [s.model_dump() for s in fire_stations.values()],
    }


@app.get("/calc/{incident_id}")
def get_calc(incident_id: str):
    return {"steps": calc_log.get(incident_id, [])}


@app.post("/incidents/")
async def report_incident(incident_type: str, lat: float, lng: float):
    incident_id   = f"INC-{uuid.uuid4().hex[:6].upper()}"
    reported_time = datetime.now()

    # UNPACK THE 3rd VARIABLE HERE
    priority_score, units_needed, preferred_caps = evaluate_incident(incident_type, reported_time)

    new_incident = Incident(
        incident_id=incident_id,
        type=incident_type,
        priority=priority_score,
        required_units=units_needed,
        preferred_capabilities=preferred_caps, # PASS IT TO INCIDENT HERE
        location=Location(lat=lat, lng=lng),
        reported_time=reported_time,
    )

    assigned_details, calc_steps = optimize_dispatch(new_incident, fleet_units, fire_stations)

    new_incident.assigned_units   = [d["unit_id"] for d in assigned_details]
    new_incident.dispatch_details = assigned_details
    new_incident.status           = "Dispatched" if assigned_details else "Pending"

    active_incidents[incident_id] = new_incident
    calc_log[incident_id]         = calc_steps

    await manager.broadcast(json.dumps({
        "type": "NEW_INCIDENT",
        "incident_id": incident_id,
    }))
    return {"message": "Processed", "incident": new_incident.model_dump(), "calc": calc_steps}


@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    if incident_id not in active_incidents:
        return {"error": "Not found"}

    incident = active_incidents[incident_id]
    incident.status = "Resolved"

    for unit_id in incident.assigned_units:
        if unit_id in fleet_units:
            fleet_units[unit_id].status = "Available"

    calc_log.pop(incident_id, None)

    # --- NEW: SAVE TO AUDIT LOG ---
    resolved_time = datetime.now()
    duration_mins = round((resolved_time - incident.reported_time).total_seconds() / 60, 2)
    
    audit_log_db.append({
        "incident_id": incident.incident_id,
        "type": incident.type,
        "priority": incident.priority,
        "units": incident.assigned_units,
        "duration_mins": duration_mins,
        "reported_at": incident.reported_time.strftime("%H:%M:%S")
    })
    # ------------------------------

    await manager.broadcast(json.dumps({
        "type": "INCIDENT_RESOLVED",
        "incident_id": incident_id,
    }))
    return {"message": "Resolved", "incident": incident.model_dump()}

@app.post("/incidents/{incident_id}/return")
async def return_to_base(incident_id: str):
    """Calculates return routes to home stations for all deployed units"""
    if incident_id not in active_incidents:
        return {"error": "Not found"}

    incident = active_incidents[incident_id]
    incident.status = "Returning"

    new_dispatch_details = []
    
    for unit_id in incident.assigned_units:
        if unit_id in fleet_units:
            unit = fleet_units[unit_id]
            unit.status = "Returning"
            station = fire_stations[unit.station_id]

            # Ask OSRM for the route BACK to the station
            dist_miles, route_latlng, duration_s, is_road = _get_route(
                incident.location.lat, incident.location.lng,
                station.location.lat, station.location.lng
            )

            new_dispatch_details.append({
                "unit_id": unit_id,
                "unit_type": unit.type,
                "station_name": station.name,
                "distance": dist_miles,
                "duration_s": duration_s,
                "route_shape": route_latlng,
                "is_road_route": is_road,
            })

    # Overwrite the routes with the return trips
    incident.dispatch_details = new_dispatch_details

    await manager.broadcast(json.dumps({
        "type": "STATUS_UPDATE",
        "incident_id": incident_id,
    }))
    return {"message": "Returning to base", "incident": incident.model_dump()}

# --- NEW ENDPOINT FOR THE FRONTEND ---
@app.get("/audit-log")
def get_audit_log():
    return {"log": audit_log_db[::-1]} # Return newest first

@app.post("/incidents/{incident_id}/on-scene")
async def mark_on_scene(incident_id: str):
    """Automatically triggered when the frontend vehicle animation finishes driving"""
    if incident_id not in active_incidents:
        return {"error": "Incident not found"}
    
    incident = active_incidents[incident_id]
    
    # Only update if it's currently Dispatched to avoid overwriting a Resolved status
    if incident.status == "Dispatched":
        incident.status = "On Scene"
        
        # Update the specific units
        for unit_id in incident.assigned_units:
            if unit_id in fleet_units:
                fleet_units[unit_id].status = "On Scene"
                
        # Broadcast the status change to React immediately
        await manager.broadcast(json.dumps({"type": "STATUS_UPDATE", "incident_id": incident_id}))
        
    return {"message": "Units on scene", "incident": incident}

@app.post("/incidents/{incident_id}/transport")
async def mark_transporting(incident_id: str):
    """Triggered when units leave the scene to go to the hospital or return to station"""
    if incident_id not in active_incidents:
        return {"error": "Incident not found"}
    
    incident = active_incidents[incident_id]
    
    if incident.status == "On Scene":
        incident.status = "Transporting"
        for unit_id in incident.assigned_units:
            if unit_id in fleet_units:
                fleet_units[unit_id].status = "Transporting"
                
        await manager.broadcast(json.dumps({"type": "STATUS_UPDATE", "incident_id": incident_id}))
        
    return {"message": "Units transporting", "incident": incident}

@app.get("/units")
def get_units():
    return [u.model_dump() for u in fleet_units.values()]

from datetime import timedelta
import random

@app.get("/forecast")
async def get_demand_forecast():
    # Generate 6 time buckets (15 min intervals for the next 90 mins)
    now = datetime.now()
    data = []
    
    # Base our forecast roughly on current active incidents
    base_engine = sum(1 for inc in active_incidents.values() if "Engine" in inc.required_units)
    base_med = sum(1 for inc in active_incidents.values() if "Ambulance" in inc.required_units)
    
    for i in range(6):
        time_label = (now + timedelta(minutes=15 * (i+1))).strftime("%H:%M")
        
        # Add a slight randomized trend curve to simulate AI predictions
        trend = random.choice([-1, 0, 1, 2])
        
        data.append({
            "time": time_label,
            "Engines": max(0, base_engine + trend + random.randint(0, 2)),
            "Ambulances": max(0, base_med + trend + random.randint(0, 2))
        })
        
    return {"forecast": data}