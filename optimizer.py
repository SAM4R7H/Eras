"""Dispatch optimizer for emergency response units."""

import math
import time
from typing import List, Dict, Tuple, Any, Optional
from dataclasses import dataclass

import requests

try:
    from ortools.linear_solver import pywraplp
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False


# OSRM routing service configuration
OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/driving"
OSRM_TIMEOUT_SECONDS = 4
EARTH_MILES_PER_DEGREE = 69.0
METERS_PER_MILE = 1609.34

# Capability matching weights
# ALS units get a distance discount for critical medical incidents
ALS_DISTANCE_DISCOUNT = 0.7  # ALS units appear 30% closer for P1 medical
BLS_DISTANCE_DISCOUNT = 1.0  # BLS units at actual distance


@dataclass
class RouteResult:
    """Result of a route calculation."""
    distance_miles: float
    route_coordinates: List[List[float]]
    duration_seconds: Optional[float]
    is_road_route: bool


def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate Euclidean distance between two coordinates (in degrees).
    
    Note: This is an approximation. For production use, consider Haversine formula.
    """
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)


def deg_to_miles(degrees: float) -> float:
    """Convert degrees to miles."""
    return round(degrees * EARTH_MILES_PER_DEGREE, 2)


def _get_route(lat1: float, lon1: float, lat2: float, lon2: float) -> RouteResult:
    """
    Get route information from OSRM routing service.
    
    Falls back to straight-line distance if OSRM is unavailable.
    """
    osrm_url = (
        f"{OSRM_BASE_URL}/{lon1},{lat1};{lon2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    
    try:
        response = requests.get(osrm_url, timeout=OSRM_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
        
        route = data["routes"][0]
        distance_miles = round(route["distance"] / METERS_PER_MILE, 2)
        duration_seconds = round(route["duration"], 1)
        # Convert GeoJSON [lng, lat] to [lat, lng] format
        route_coordinates = [[coord[1], coord[0]] for coord in route["geometry"]["coordinates"]]
        
        return RouteResult(
            distance_miles=distance_miles,
            route_coordinates=route_coordinates,
            duration_seconds=duration_seconds,
            is_road_route=True
        )
    except Exception:
        # Fallback to straight-line distance
        straight_distance = deg_to_miles(calculate_distance(lat1, lon1, lat2, lon2))
        return RouteResult(
            distance_miles=straight_distance,
            route_coordinates=[[lat1, lon1], [lat2, lon2]],
            duration_seconds=None,
            is_road_route=False
        )


def _score_candidates(
    available_units: List[Any],
    incident: Any,
    fire_stations: Dict[str, Any],
    capability_requirement: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Score and sort available units by distance to incident.
    
    Args:
        available_units: List of available unit objects
        incident: The incident requiring response
        fire_stations: Dictionary of fire stations
        capability_requirement: 'ALS', 'ALS_preferred', or None
    
    Returns:
        List of scored candidate dictionaries
    """
    scored = []
    
    for unit in available_units:
        station = fire_stations[unit.station_id]
        distance_deg = calculate_distance(
            incident.location.lat, incident.location.lng,
            station.location.lat, station.location.lng
        )
        
        # Apply capability-based scoring for medical incidents
        distance_modifier = 1.0
        capability_note = ""
        unit_cap = getattr(unit, 'capability', None)
        
        if capability_requirement and unit_cap:
            if capability_requirement == "ALS":
                if unit_cap == "ALS":
                    distance_modifier = ALS_DISTANCE_DISCOUNT
                    capability_note = " (ALS matched)"
                elif unit_cap == "BLS":
                    distance_modifier = 1.3  # BLS penalized for P1
                    capability_note = " (BLS - not preferred)"
            elif capability_requirement == "ALS_preferred":
                if unit_cap == "ALS":
                    distance_modifier = ALS_DISTANCE_DISCOUNT
                    capability_note = " (ALS preferred)"
        
        modified_distance = distance_deg * distance_modifier
        
        scored.append({
            "unit_id": unit.unit_id,
            "station": station.name,
            "dist_deg": round(distance_deg, 5),
            "dist_miles": deg_to_miles(distance_deg),
            "cost_score": round(modified_distance * 1000, 3),
            "capability": str(unit_cap) if unit_cap else "None",
            "capability_note": capability_note,
        })
    
    # Sort by modified cost score (closest/preferred first)
    scored.sort(key=lambda x: x["cost_score"])
    return scored


def _pick_units_ortools(
    available_units: List[Any],
    count_needed: int,
    incident: Any,
    fire_stations: Dict[str, Any]
) -> Tuple[List[Any], str]:
    """Select optimal units using OR-Tools integer programming solver."""
    solver = pywraplp.Solver.CreateSolver("CBC_MIXED_INTEGER_PROGRAMMING")
    
    if not solver:
        return (
            _pick_units_greedy(available_units, count_needed, incident, fire_stations),
            "Greedy (solver init failed)"
        )

    # Create binary decision variables for each unit
    unit_vars = {
        unit.unit_id: solver.IntVar(0, 1, f"x_{unit.unit_id}")
        for unit in available_units
    }
    
    # Constraint: select exactly count_needed units
    solver.Add(sum(unit_vars[unit.unit_id] for unit in available_units) == count_needed)
    
    # Objective: minimize total distance
    solver.Minimize(sum(
        calculate_distance(
            incident.location.lat, incident.location.lng,
            fire_stations[unit.station_id].location.lat,
            fire_stations[unit.station_id].location.lng,
        ) * unit_vars[unit.unit_id]
        for unit in available_units
    ))

    solve_start = time.perf_counter()
    status = solver.Solve()
    solve_time_ms = round((time.perf_counter() - solve_start) * 1000, 2)

    if status == pywraplp.Solver.OPTIMAL:
        chosen_units = [
            unit for unit in available_units
            if unit_vars[unit.unit_id].solution_value() > 0.5
        ]
        objective_value = round(solver.Objective().Value() * 1000, 3)
        return chosen_units, f"OR-Tools CBC | obj={objective_value} | {solve_time_ms}ms"

    return (
        _pick_units_greedy(available_units, count_needed, incident, fire_stations),
        "Greedy (ILP non-optimal)"
    )


def _pick_units_greedy(
    available_units: List[Any],
    count_needed: int,
    incident: Any,
    fire_stations: Dict[str, Any]
) -> List[Any]:
    """Select closest units using greedy approach."""
    sorted_units = sorted(
        available_units,
        key=lambda unit: calculate_distance(
            incident.location.lat, incident.location.lng,
            fire_stations[unit.station_id].location.lat,
            fire_stations[unit.station_id].location.lng,
        )
    )
    return sorted_units[:count_needed]


def optimize_dispatch(
    incident: Any,
    fleet_units: Dict[str, Any],
    fire_stations: Dict[str, Any],
    capability_requirement: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Optimize dispatch of units to an incident.
    
    Args:
        incident: The incident requiring response
        fleet_units: Dictionary of all fleet units
        fire_stations: Dictionary of all fire stations
        capability_requirement: Medical capability requirement ('ALS', 'ALS_preferred', or None)
        
    Returns:
        Tuple of (dispatched_details, calculation_steps)
    """
    dispatched_details = []
    calculation_steps = []

    for unit_type, count_needed in incident.required_units.items():
        # Find available units of the required type
        available_units = [
            unit for unit in fleet_units.values()
            if unit.type == unit_type and unit.status == "Available"
        ]

        if not available_units:
            calculation_steps.append({
                "unit_type": unit_type,
                "needed": count_needed,
                "available": 0,
                "candidates": [],
                "solver": "N/A — no units available",
                "selected": [],
            })
            continue

        # Adjust count if fewer units available than needed
        actual_count_needed = min(count_needed, len(available_units))

        # Score all candidates with capability matching
        candidates = _score_candidates(available_units, incident, fire_stations, capability_requirement)
        solver_label = "Greedy (n=1)"

        # Select units using appropriate algorithm
        if HAS_ORTOOLS and actual_count_needed > 1:
            chosen_units, solver_label = _pick_units_ortools(
                available_units, actual_count_needed, incident, fire_stations
            )
        else:
            chosen_units = _pick_units_greedy(
                available_units, actual_count_needed, incident, fire_stations
            )
            if not HAS_ORTOOLS:
                solver_label = "Greedy (OR-Tools not installed)"

        # Mark selected candidates
        chosen_ids = {unit.unit_id for unit in chosen_units}
        for candidate in candidates:
            candidate["selected"] = candidate["unit_id"] in chosen_ids

        calculation_steps.append({
            "unit_type": unit_type,
            "needed": actual_count_needed,
            "available": len(available_units),
            "candidates": candidates,
            "solver": solver_label,
            "selected": list(chosen_ids),
        })

        # Process chosen units
        for unit in chosen_units:
            unit.status = "Dispatched"
            station = fire_stations[unit.station_id]
            
            route_result = _get_route(
                station.location.lat, station.location.lng,
                incident.location.lat, incident.location.lng,
            )
            
            dispatched_details.append({
                "unit_id": unit.unit_id,
                "unit_type": unit.type,
                "station_name": station.name,
                "distance": route_result.distance_miles,
                "duration_s": route_result.duration_seconds,
                "route_shape": route_result.route_coordinates,
                "is_road_route": route_result.is_road_route,
            })

    return dispatched_details, calculation_steps
