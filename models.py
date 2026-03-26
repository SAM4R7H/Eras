"""Data models for the AI CAD System."""

from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Dict
from datetime import datetime
from enum import Enum


class UnitStatus(str, Enum):
    """Enum for unit status values."""
    AVAILABLE = "Available"
    DISPATCHED = "Dispatched"
    EN_ROUTE = "En Route"
    ON_SCENE = "On Scene"
    TRANSPORTING = "Transporting"
    MAINTENANCE = "Maintenance"
    OFFLINE = "Offline"


class IncidentStatus(str, Enum):
    """Enum for incident status values."""
    PENDING = "Pending"
    DISPATCHED = "Dispatched"
    EN_ROUTE = "En Route"
    ON_SCENE = "On Scene"
    TRANSPORTING = "Transporting"
    RESOLVED = "Resolved"
    CANCELLED = "Cancelled"


class UnitType(str, Enum):
    """Enum for valid unit types."""
    ENGINE = "Engine"
    AMBULANCE = "Ambulance"
    LADDER = "Ladder"
    RESCUE = "Rescue"
    HAZMAT = "Hazmat"


class MedicalCapability(str, Enum):
    """Medical capability levels for ambulances."""
    ALS = "ALS"  # Advanced Life Support
    BLS = "BLS"  # Basic Life Support
    NONE = "None"  # Non-medical units


class Location(BaseModel):
    """Geographic location with latitude and longitude."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude in degrees")
    lng: float = Field(..., ge=-180, le=180, description="Longitude in degrees")


class Unit(BaseModel):
    """Represents a fleet unit (vehicle/equipment)."""
    unit_id: str = Field(..., min_length=1, description="Unique unit identifier")
    type: UnitType | str = Field(..., description="Unit type (e.g., Engine, Ambulance)")
    station_id: str = Field(..., min_length=1, description="Home station identifier")
    status: UnitStatus | str = Field(default=UnitStatus.AVAILABLE, description="Current unit status")
    location: Optional[Location] = Field(default=None, description="Current GPS location")
    capability: Optional[MedicalCapability | str] = Field(default=MedicalCapability.NONE, description="Medical capability level (for ambulances)")
    dispatched_time: Optional[datetime] = Field(default=None, description="Time when unit was dispatched")
    arrived_time: Optional[datetime] = Field(default=None, description="Time when unit arrived on scene")

    class Config:
        use_enum_values = True


class Station(BaseModel):
    """Represents a fire/emergency station."""
    station_id: str = Field(..., min_length=1, description="Unique station identifier")
    name: str = Field(..., min_length=1, description="Station name")
    location: Location = Field(..., description="Station geographic location")
    units: List[str] = Field(default_factory=list, description="List of unit IDs at this station")


class DispatchDetail(BaseModel):
    """Details about a unit's dispatch to an incident."""
    unit_id: str = Field(..., description="Dispatched unit ID")
    unit_type: str = Field(..., description="Type of dispatched unit")
    station_name: str = Field(..., description="Dispatching station name")
    distance: float = Field(..., ge=0, description="Distance to incident in miles")
    duration_s: Optional[float] = Field(default=None, ge=0, description="Estimated travel time in seconds")
    route_shape: List[List[float]] = Field(default_factory=list, description="Route coordinates [lat, lng]")
    is_road_route: bool = Field(default=False, description="Whether route uses road network")


class Incident(BaseModel):
    """Represents an emergency incident."""
    incident_id: str = Field(..., description="Unique incident identifier")
    type: str = Field(..., description="Incident type (e.g., Fire, Medical)")
    priority: Optional[int] = Field(default=None, ge=0, le=100, description="Priority score 0-100")
    location: Location = Field(..., description="Incident location")
    reported_time: datetime = Field(..., description="Time incident was reported")
    status: IncidentStatus | str = Field(default=IncidentStatus.PENDING, description="Current incident status")
    required_units: Optional[Dict[str, int]] = Field(default=None, description="Required unit types and counts")
    assigned_units: List[str] = Field(default_factory=list, description="List of assigned unit IDs")
    dispatch_details: List[DispatchDetail] = Field(
        default_factory=list,
        description="Detailed dispatch information including routes"
    )
    # Golden hour tracking
    golden_hour_end: Optional[datetime] = Field(default=None, description="End of golden hour window (reported_time + 60 min)")
    # Timeline for audit log
    timeline: List[Dict[str, str]] = Field(default_factory=list, description="Timeline of events for audit")
    resolved_time: Optional[datetime] = Field(default=None, description="Time when incident was resolved")
    total_distance: Optional[float] = Field(default=None, ge=0, description="Total distance travelled by all units in miles")

    class Config:
        use_enum_values = True


class AuditLogEntry(BaseModel):
    """Audit log entry for a resolved incident."""
    incident_id: str = Field(..., description="Unique incident identifier")
    type: str = Field(..., description="Incident type")
    priority: int = Field(..., ge=0, le=100, description="Priority score")
    location: Location = Field(..., description="Incident location")
    reported_time: datetime = Field(..., description="Time incident was reported")
    resolved_time: datetime = Field(..., description="Time incident was resolved")
    status: str = Field(..., description="Final status")
    assigned_units: List[str] = Field(..., description="List of assigned unit IDs")
    dispatch_details: List[DispatchDetail] = Field(..., description="Dispatch details")
    timeline: List[Dict[str, str]] = Field(..., description="Timeline of events")
    total_distance: float = Field(..., ge=0, description="Total distance travelled in miles")
    response_time_seconds: Optional[float] = Field(default=None, ge=0, description="Time from report to first unit arrival")


class StagingZone(BaseModel):
    """Pre-defined staging zone for redeployment."""
    zone_id: str = Field(..., description="Unique zone identifier")
    name: str = Field(..., description="Zone name (e.g., Mansarovar, Civil Lines)")
    location: Location = Field(..., description="Staging zone location")
    priority: int = Field(default=1, ge=1, description="Priority level for coverage")
