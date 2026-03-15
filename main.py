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


# ── WebSocket manager ─────────────────────────────────────────────
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


# ── Seed data ─────────────────────────────────────────────────────
def seed_data():
    fire_stations["ST-1"] = Station(
        station_id="ST-1", name="Downtown Station",
        location=Location(lat=40.7128, lng=-74.0060),
        units=["ENG-1", "MED-1"],
    )
    fleet_units["ENG-1"] = Unit(unit_id="ENG-1", type="Engine",    station_id="ST-1")
    fleet_units["MED-1"] = Unit(unit_id="MED-1", type="Ambulance", station_id="ST-1")

    fire_stations["ST-2"] = Station(
        station_id="ST-2", name="Uptown Station",
        location=Location(lat=40.7306, lng=-73.9866),
        units=["ENG-2", "LAD-1"],
    )
    fleet_units["ENG-2"] = Unit(unit_id="ENG-2", type="Engine", station_id="ST-2")
    fleet_units["LAD-1"] = Unit(unit_id="LAD-1", type="Ladder", station_id="ST-2")

    # FIX: extra ambulance so Medical incidents always have a unit available
    fire_stations["ST-3"] = Station(
        station_id="ST-3", name="Midtown Station",
        location=Location(lat=40.7549, lng=-73.9840),
        units=["MED-2", "ENG-3"],
    )
    fleet_units["MED-2"] = Unit(unit_id="MED-2", type="Ambulance", station_id="ST-3")
    fleet_units["ENG-3"] = Unit(unit_id="ENG-3", type="Engine",    station_id="ST-3")


seed_data()


# ── Endpoints ─────────────────────────────────────────────────────
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

    assigned_details = optimize_dispatch(new_incident, fleet_units, fire_stations)

    new_incident.assigned_units   = [d["unit_id"] for d in assigned_details]
    new_incident.dispatch_details = assigned_details
    new_incident.status           = "Dispatched" if assigned_details else "Pending"

    active_incidents[incident_id] = new_incident

    await manager.broadcast(json.dumps({
        "type": "NEW_INCIDENT",
        "incident_id": incident_id,
    }))
    return {"message": "Processed", "incident": new_incident.model_dump()}


@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    if incident_id not in active_incidents:
        return {"error": "Not found"}

    incident = active_incidents[incident_id]
    incident.status = "Resolved"

    # FIX: reset ALL assigned units back to Available
    # Previously only reset units still in fleet_units — now also catches
    # units that were marked Dispatched and need to return to standby.
    for unit_id in incident.assigned_units:
        if unit_id in fleet_units:
            fleet_units[unit_id].status = "Available"

    await manager.broadcast(json.dumps({
        "type": "INCIDENT_RESOLVED",
        "incident_id": incident_id,
    }))
    return {"message": "Resolved", "incident": incident.model_dump()}


@app.get("/units")
def get_units():
    """Quick endpoint to check unit statuses during debugging."""
    return [u.model_dump() for u in fleet_units.values()]
