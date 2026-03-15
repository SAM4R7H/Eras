
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

class Location(BaseModel):
    lat: float
    lng: float

class Unit(BaseModel):
    unit_id: str
    type: str  # e.g., "Engine", "Ambulance", "Ladder"
    station_id: str
    status: str = "Available"
    location: Optional[Location] = None

class Station(BaseModel):
    station_id: str
    name: str
    location: Location
    units: List[str] = []

class Incident(BaseModel):
    incident_id: str
    type: str
    priority: Optional[int] = None 
    location: Location
    reported_time: datetime
    status: str = "Pending" 
    required_units: Optional[dict] = None 
    assigned_units: List[str] = []
    dispatch_details: list = []  # Holds the math & OSRM street route
