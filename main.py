from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from models import Incident, Unit, Station, Location
import uuid
from datetime import datetime
from ai_engine import evaluate_incident
from optimizer import optimize_dispatch
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


def seed_data():
    fire_stations["ST-1"] = Station(
        station_id="ST-1", name="Civil Lines Station",
        location=Location(lat=26.9260, lng=75.8235),
        units=["ENG-1", "MED-1"],
    )
    fleet_units["ENG-1"] = Unit(unit_id="ENG-1", type="Engine",    station_id="ST-1")
    fleet_units["MED-1"] = Unit(unit_id="MED-1", type="Ambulance", station_id="ST-1")

    fire_stations["ST-2"] = Station(
        station_id="ST-2", name="Vaishali Nagar Station",
        location=Location(lat=26.9124, lng=75.7373),
        units=["ENG-2", "LAD-1"],
    )
    fleet_units["ENG-2"] = Unit(unit_id="ENG-2", type="Engine", station_id="ST-2")
    fleet_units["LAD-1"] = Unit(unit_id="LAD-1", type="Ladder", station_id="ST-2")

    fire_stations["ST-3"] = Station(
        station_id="ST-3", name="Mansarovar Station",
        location=Location(lat=26.8535, lng=75.7726),
        units=["MED-2", "ENG-3"],
    )
    fleet_units["MED-2"] = Unit(unit_id="MED-2", type="Ambulance", station_id="ST-3")
    fleet_units["ENG-3"] = Unit(unit_id="ENG-3", type="Engine",    station_id="ST-3")

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

    priority_score, units_needed = evaluate_incident(incident_type, reported_time)

    new_incident = Incident(
        incident_id=incident_id,
        type=incident_type,
        priority=priority_score,
        required_units=units_needed,
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

    await manager.broadcast(json.dumps({
        "type": "INCIDENT_RESOLVED",
        "incident_id": incident_id,
    }))
    return {"message": "Resolved", "incident": incident.model_dump()}


@app.get("/units")
def get_units():
    return [u.model_dump() for u in fleet_units.values()]
