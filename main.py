"""Main FastAPI application for the AI CAD (Computer-Aided Dispatch) System."""

import json
import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from models import Incident, Unit, Station, Location, IncidentStatus, UnitStatus, MedicalCapability, AuditLogEntry, StagingZone
from ai_engine import evaluate_incident, get_capability_requirement
from optimizer import optimize_dispatch
from datetime import timedelta

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="AI CAD System API",
    description="AI-powered Computer-Aided Dispatch system for emergency response",
    version="1.0.0"
)

# CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    """Manages WebSocket connections for real-time updates."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: str) -> None:
        """Broadcast a message to all connected clients."""
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                dead_connections.append(connection)
        
        # Clean up dead connections
        for connection in dead_connections:
            self.disconnect(connection)


# Global instances
manager = ConnectionManager()
active_incidents: Dict[str, Incident] = {}
fleet_units: Dict[str, Unit] = {}
fire_stations: Dict[str, Station] = {}
calculation_log: Dict[str, List[dict]] = {}
audit_log: List[AuditLogEntry] = []  # Post-incident audit log
staging_zones: Dict[str, StagingZone] = {}  # Redeployment staging zones

# Scenario mode state
scenario_mode = False
scenario_incidents: List[Dict[str, any]] = []  # Temporary incidents for what-if analysis


def seed_data() -> None:
    """Initialize sample data for stations and units."""
    # Station 1: Civil Lines
    fire_stations["ST-1"] = Station(
        station_id="ST-1",
        name="Civil Lines Station",
        location=Location(lat=26.9260, lng=75.8235),
        units=["ENG-1", "MED-1"],
    )
    fleet_units["ENG-1"] = Unit(unit_id="ENG-1", type="Engine", station_id="ST-1", capability="None")
    fleet_units["MED-1"] = Unit(unit_id="MED-1", type="Ambulance", station_id="ST-1", capability="ALS")

    # Station 2: Vaishali Nagar
    fire_stations["ST-2"] = Station(
        station_id="ST-2",
        name="Vaishali Nagar Station",
        location=Location(lat=26.9124, lng=75.7373),
        units=["ENG-2", "LAD-1"],
    )
    fleet_units["ENG-2"] = Unit(unit_id="ENG-2", type="Engine", station_id="ST-2", capability="None")
    fleet_units["LAD-1"] = Unit(unit_id="LAD-1", type="Ladder", station_id="ST-2", capability="None")

    # Station 3: Mansarovar
    fire_stations["ST-3"] = Station(
        station_id="ST-3",
        name="Mansarovar Station",
        location=Location(lat=26.8535, lng=75.7726),
        units=["MED-2", "ENG-3"],
    )
    fleet_units["MED-2"] = Unit(unit_id="MED-2", type="Ambulance", station_id="ST-3", capability="BLS")
    fleet_units["ENG-3"] = Unit(unit_id="ENG-3", type="Engine", station_id="ST-3", capability="None")

    # Define staging zones for redeployment (Jaipur areas)
    staging_zones["SZ-1"] = StagingZone(zone_id="SZ-1", name="Mansarovar", location=Location(lat=26.8535, lng=75.7726), priority=1)
    staging_zones["SZ-2"] = StagingZone(zone_id="SZ-2", name="Civil Lines", location=Location(lat=26.9260, lng=75.8235), priority=2)
    staging_zones["SZ-3"] = StagingZone(zone_id="SZ-3", name="Vaishali Nagar", location=Location(lat=26.9124, lng=75.7373), priority=1)
    staging_zones["SZ-4"] = StagingZone(zone_id="SZ-4", name="Sindhi Camp", location=Location(lat=26.9180, lng=75.8150), priority=3)

    logger.info("Seed data initialized with 3 stations, 6 units, and 4 staging zones")


# Initialize seed data on startup
seed_data()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle WebSocket connections for real-time updates."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
            # Keep-alive: client can send periodic pings
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.get("/system-status")
def get_system_status():
    """Get current status of all incidents, units, and stations."""
    return {
        "active_incidents": [incident.model_dump() for incident in active_incidents.values()],
        "units": [unit.model_dump() for unit in fleet_units.values()],
        "stations": [station.model_dump() for station in fire_stations.values()],
    }


@app.get("/calc/{incident_id}")
def get_calculation_steps(incident_id: str):
    """Get calculation steps for a specific incident."""
    if incident_id not in active_incidents:
        raise HTTPException(status_code=404, detail="Incident not found")
    
    return {"steps": calculation_log.get(incident_id, [])}


@app.post("/incidents/")
async def create_incident(
    incident_type: str = Query(..., description="Type of incident (e.g., Fire, Medical)"),
    lat: float = Query(..., ge=-90, le=90, description="Latitude of incident"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude of incident")
):
    """
    Report a new incident and dispatch appropriate units.
    
    This endpoint:
    1. Evaluates incident priority and required units
    2. Optimizes unit dispatch using distance-based algorithm with capability matching
    3. Returns incident details with dispatch information
    """
    incident_id = f"INC-{uuid.uuid4().hex[:6].upper()}"
    reported_time = datetime.now()

    # Evaluate incident to determine priority and required units
    priority_score, units_needed = evaluate_incident(incident_type, reported_time)
    logger.info(f"Incident {incident_id}: Type={incident_type}, Priority={priority_score}")

    # Determine capability requirement for medical incidents
    capability_req = get_capability_requirement(incident_type, priority_score)

    # Create incident record with golden hour tracking for P1 incidents
    new_incident = Incident(
        incident_id=incident_id,
        type=incident_type,
        priority=priority_score,
        required_units=units_needed,
        location=Location(lat=lat, lng=lng),
        reported_time=reported_time,
        golden_hour_end=reported_time + timedelta(minutes=60) if priority_score >= 80 else None,
        timeline=[{"event": "Incident reported", "timestamp": reported_time.isoformat()}]
    )

    # Optimize dispatch with capability matching
    assigned_details, calculation_steps = optimize_dispatch(
        new_incident, fleet_units, fire_stations, capability_req
    )

    # Add capability notes to calculation steps
    if capability_req:
        for step in calculation_steps:
            if step["unit_type"] == "Ambulance":
                step["capability_requirement"] = capability_req
                for candidate in step.get("candidates", []):
                    if candidate.get("capability_note"):
                        step.setdefault("notes", []).append(
                            f"{candidate['unit_id']}: {candidate['capability_note']}"
                        )

    # Update incident with dispatch information
    new_incident.assigned_units = [detail["unit_id"] for detail in assigned_details]
    new_incident.dispatch_details = assigned_details
    new_incident.status = IncidentStatus.DISPATCHED if assigned_details else IncidentStatus.PENDING
    
    # Calculate total distance
    new_incident.total_distance = sum(d["distance"] for d in assigned_details)
    
    # Add dispatch event to timeline
    new_incident.timeline.append({
        "event": "Units dispatched",
        "timestamp": datetime.now().isoformat(),
        "units": new_incident.assigned_units
    })

    # Mark units as dispatched with timestamp
    for unit_id in new_incident.assigned_units:
        if unit_id in fleet_units:
            fleet_units[unit_id].status = UnitStatus.DISPATCHED
            fleet_units[unit_id].dispatched_time = datetime.now()

    # Store incident and calculation log
    active_incidents[incident_id] = new_incident
    calculation_log[incident_id] = calculation_steps

    # Broadcast to connected clients
    await manager.broadcast(json.dumps({
        "type": "NEW_INCIDENT",
        "incident_id": incident_id,
    }))

    logger.info(f"Incident {incident_id} processed with {len(assigned_details)} units dispatched")
    
    return {
        "message": "Incident processed successfully",
        "incident": new_incident.model_dump(),
        "calculation": calculation_steps
    }


@app.post("/incidents/{incident_id}/resolve")
async def resolve_incident(incident_id: str):
    """
    Mark an incident as resolved, create audit log entry, and trigger redeployment.
    
    This endpoint:
    1. Updates incident status through the lifecycle (On Scene -> Transporting -> Resolved)
    2. Creates an audit log entry with full timeline
    3. Releases units and triggers redeployment to staging zones
    """
    if incident_id not in active_incidents:
        raise HTTPException(status_code=404, detail="Incident not found")

    incident = active_incidents[incident_id]
    resolved_time = datetime.now()
    
    # Update unit statuses through lifecycle
    for unit_id in incident.assigned_units:
        if unit_id in fleet_units:
            unit = fleet_units[unit_id]
            # Transition: On Scene -> Transporting -> Available
            if unit.status == UnitStatus.DISPATCHED:
                unit.status = UnitStatus.ON_SCENE
                unit.arrived_time = resolved_time
                incident.timeline.append({
                    "event": f"{unit_id} arrived on scene",
                    "timestamp": resolved_time.isoformat()
                })
            
            # Brief transporting status before release
            unit.status = UnitStatus.TRANSPORTING
            incident.timeline.append({
                "event": f"{unit_id} transporting",
                "timestamp": resolved_time.isoformat()
            })
            
            # Release back to available
            unit.status = UnitStatus.AVAILABLE
            unit.dispatched_time = None
            unit.arrived_time = None

    # Calculate response time (first unit arrival)
    response_time_seconds = None
    if incident.timeline:
        report_time = incident.reported_time
        for event in incident.timeline:
            if "arrived" in event.get("event", "").lower():
                arrival_time = datetime.fromisoformat(event["timestamp"])
                response_time_seconds = (arrival_time - report_time).total_seconds()
                break

    # Create audit log entry
    audit_entry = AuditLogEntry(
        incident_id=incident.incident_id,
        type=incident.type,
        priority=incident.priority or 0,
        location=incident.location,
        reported_time=incident.reported_time,
        resolved_time=resolved_time,
        status="Resolved",
        assigned_units=incident.assigned_units.copy(),
        dispatch_details=incident.dispatch_details.copy(),
        timeline=incident.timeline.copy(),
        total_distance=incident.total_distance or 0.0,
        response_time_seconds=response_time_seconds
    )
    audit_log.append(audit_entry)

    # Update incident final state
    incident.status = IncidentStatus.RESOLVED
    incident.resolved_time = resolved_time
    incident.timeline.append({
        "event": "Incident resolved",
        "timestamp": resolved_time.isoformat()
    })

    # Trigger redeployment to staging zones
    redeployed_units = []
    for unit_id in incident.assigned_units:
        if unit_id in fleet_units:
            target_zone = find_nearest_undercovered_staging_zone(fleet_units[unit_id], fire_stations, staging_zones)
            if target_zone:
                redeployed_units.append({
                    "unit_id": unit_id,
                    "target_zone": target_zone.name,
                    "zone_location": {"lat": target_zone.location.lat, "lng": target_zone.location.lng}
                })

    # Remove from calculation log but keep in active_incidents for history view
    calculation_log.pop(incident_id, None)

    # Broadcast resolution
    await manager.broadcast(json.dumps({
        "type": "INCIDENT_RESOLVED",
        "incident_id": incident_id,
        "redeployments": redeployed_units
    }))

    logger.info(f"Incident {incident_id} resolved. Audit log created. Units redeployed: {[r['unit_id'] for r in redeployed_units]}")
    
    return {
        "message": "Incident resolved successfully",
        "incident": incident.model_dump(),
        "audit_entry": audit_entry.model_dump(),
        "redeployments": redeployed_units
    }


def find_nearest_undercovered_staging_zone(unit: Unit, stations: Dict[str, Station], zones: Dict[str, StagingZone]) -> Optional[StagingZone]:
    """Find the nearest staging zone that currently has weak coverage."""
    if not hasattr(unit, 'location') or not unit.location:
        # Use station location as fallback
        station = stations.get(unit.station_id)
        if not station:
            return None
        unit_lat, unit_lng = station.location.lat, station.location.lng
    else:
        unit_lat, unit_lng = unit.location.lat, unit.location.lng
    
    # Simple heuristic: return nearest zone (could be enhanced with coverage analysis)
    nearest_zone = None
    min_distance = float('inf')
    
    for zone in zones.values():
        distance = math.sqrt((unit_lat - zone.location.lat) ** 2 + (unit_lng - zone.location.lng) ** 2)
        if distance < min_distance:
            min_distance = distance
            nearest_zone = zone
    
    return nearest_zone


@app.get("/incidents")
def list_incidents():
    """List all active incidents."""
    return {
        "incidents": [incident.model_dump() for incident in active_incidents.values()]
    }


@app.get("/audit-log")
def get_audit_log():
    """Get post-incident audit log with statistics."""
    if not audit_log:
        return {"entries": [], "statistics": None}
    
    # Calculate aggregate statistics
    total_incidents = len(audit_log)
    response_times = [e.response_time_seconds for e in audit_log if e.response_time_seconds is not None]
    avg_response_time = sum(response_times) / len(response_times) if response_times else None
    
    # Count by incident type
    type_counts = {}
    for entry in audit_log:
        type_counts[entry.type] = type_counts.get(entry.type, 0) + 1
    
    # Find busiest incident type
    busiest_type = max(type_counts.items(), key=lambda x: x[1])[0] if type_counts else None
    
    statistics = {
        "total_incidents": total_incidents,
        "average_response_time_seconds": round(avg_response_time, 2) if avg_response_time else None,
        "busiest_incident_type": busiest_type,
        "incident_type_breakdown": type_counts
    }
    
    return {
        "entries": [entry.model_dump() for entry in reversed(audit_log)],  # Most recent first
        "statistics": statistics
    }


@app.get("/staging-zones")
def get_staging_zones():
    """Get all staging zones for redeployment."""
    return [zone.model_dump() for zone in staging_zones.values()]


@app.get("/demand-forecast")
def get_demand_forecast():
    """
    Get predicted unit demand for the next 90 minutes.
    
    Uses rule-based calculation considering:
    - Current active incidents
    - Time of day patterns
    - Historical averages
    """
    from datetime import datetime as dt
    
    current_hour = dt.now().hour
    
    # Base demand multipliers by time of day
    hour_multipliers = {
        **{h: 0.8 for h in range(0, 7)},   # Night: low demand
        **{h: 1.0 for h in range(7, 9)},   # Morning: normal
        **{h: 1.3 for h in range(9, 11)},  # Late morning: elevated
        **{h: 1.0 for h in range(11, 17)}, # Afternoon: normal
        **{h: 1.5 for h in range(17, 20)}, # Evening rush: high
        **{h: 1.2 for h in range(20, 24)}, # Night: moderate
    }
    
    base_multiplier = hour_multipliers.get(current_hour, 1.0)
    
    # Current active P1/P2 incidents influence near-term demand
    active_critical = sum(1 for inc in active_incidents.values() if (inc.priority or 0) >= 60)
    
    # Forecast windows (15-minute intervals for 90 minutes)
    windows = []
    for i in range(6):
        window_start = i * 15
        window_end = (i + 1) * 15
        
        # Decay factor: further windows are less certain
        decay = 1.0 - (i * 0.1)
        
        # Base demand per unit type (scaled by multiplier and decay)
        ambulance_demand = round((1.5 * base_multiplier + active_critical * 0.3) * decay, 1)
        engine_demand = round((1.2 * base_multiplier + active_critical * 0.2) * decay, 1)
        ladder_demand = round((0.5 * base_multiplier) * decay, 1)
        
        windows.append({
            "window_minutes": f"{window_start}-{window_end}",
            "ambulance": max(1, int(ambulance_demand)),
            "engine": max(1, int(engine_demand)),
            "ladder": max(0, int(ladder_demand)),
            "confidence": round(decay * 100, 0)
        })
    
    return {
        "generated_at": dt.now().isoformat(),
        "current_hour": current_hour,
        "base_multiplier": base_multiplier,
        "active_critical_incidents": active_critical,
        "forecast_windows": windows
    }


@app.post("/scenario/toggle")
async def toggle_scenario_mode():
    """Toggle what-if scenario mode on/off."""
    global scenario_mode
    scenario_mode = not scenario_mode
    
    if scenario_mode:
        scenario_incidents.clear()
    
    await manager.broadcast(json.dumps({
        "type": "SCENARIO_MODE_CHANGED",
        "enabled": scenario_mode
    }))
    
    return {"scenario_mode": scenario_mode, "message": "Scenario mode enabled" if scenario_mode else "Scenario mode disabled"}


@app.post("/scenario/incident")
async def create_scenario_incident(
    incident_type: str = Query(..., description="Type of hypothetical incident"),
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lng: float = Query(..., ge=-180, le=180, description="Longitude"),
    priority: int = Query(default=70, ge=0, le=100, description="Priority score")
):
    """Create a hypothetical incident for what-if scenario analysis."""
    if not scenario_mode:
        raise HTTPException(status_code=400, detail="Scenario mode must be enabled")
    
    from ai_engine import DEFAULT_UNIT_REQUIREMENTS
    
    incident_id = f"SCN-{uuid.uuid4().hex[:4].upper()}"
    
    # Get required units for this incident type
    required_units = DEFAULT_UNIT_REQUIREMENTS.get(incident_type, {"Engine": 1}).copy()
    
    # Simulate dispatch without actually assigning units
    temp_incident = Incident(
        incident_id=incident_id,
        type=incident_type,
        priority=priority,
        required_units=required_units,
        location=Location(lat=lat, lng=lng),
        reported_time=datetime.now()
    )
    
    capability_req = get_capability_requirement(incident_type, priority)
    dispatch_details, calculation_steps = optimize_dispatch(
        temp_incident, fleet_units, fire_stations, capability_req
    )
    
    scenario_data = {
        "incident_id": incident_id,
        "type": incident_type,
        "priority": priority,
        "location": {"lat": lat, "lng": lng},
        "required_units": required_units,
        "dispatch_details": dispatch_details,
        "calculation_steps": calculation_steps,
        "estimated_total_distance": sum(d["distance"] for d in dispatch_details)
    }
    
    scenario_incidents.append(scenario_data)
    
    return {
        "message": "Scenario incident created",
        "scenario": scenario_data,
        "all_scenarios": scenario_incidents
    }


@app.get("/scenario/incidents")
def get_scenario_incidents():
    """Get all scenario incidents for current what-if analysis."""
    return {"scenarios": scenario_incidents}


@app.delete("/scenario/incidents")
async def clear_scenario_incidents():
    """Clear all scenario incidents."""
    scenario_incidents.clear()
    await manager.broadcast(json.dumps({
        "type": "SCENARIO_CLEARED"
    }))
    return {"message": "All scenarios cleared"}
