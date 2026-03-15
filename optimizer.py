import math
import requests

# FIX: import guard — ortools is optional, falls back to greedy if not installed
try:
    from ortools.linear_solver import pywraplp
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False
    print("WARNING: ortools not installed — using greedy distance fallback")


def calculate_distance(lat1, lon1, lat2, lon2):
    """Euclidean distance in degree-units (used for solver cost only)."""
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)


def _get_route(lat1, lon1, lat2, lon2):
    """
    Fetch real street route from OSRM.
    Returns (distance_miles, [[lat,lng], ...]).
    Falls back to straight line on any error.
    """
    osrm_url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    try:
        res = requests.get(osrm_url, timeout=3).json()
        route = res["routes"][0]
        dist_miles = round(route["distance"] / 1609.34, 2)
        # OSRM returns [lng, lat] — convert to [lat, lng] for Leaflet
        route_latlng = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        return dist_miles, route_latlng
    except Exception:
        # Straight-line fallback
        straight_dist = round(calculate_distance(lat1, lon1, lat2, lon2) * 69.0, 2)
        return straight_dist, [[lat1, lon1], [lat2, lon2]]


def _pick_units_ortools(available_units, count_needed, incident, fire_stations):
    """Use OR-Tools integer solver to pick optimal units."""
    # FIX: was 'SCIP' — SCIP is not included in the pip ortools package.
    # CBC_MIXED_INTEGER_PROGRAMMING is always available.
    solver = pywraplp.Solver.CreateSolver("CBC_MIXED_INTEGER_PROGRAMMING")
    if not solver:
        return _pick_units_greedy(available_units, count_needed, incident, fire_stations)

    x = {u.unit_id: solver.IntVar(0, 1, f"x_{u.unit_id}") for u in available_units}

    # Exactly count_needed units must be selected
    solver.Add(sum(x[u.unit_id] for u in available_units) == count_needed)

    # Minimise total distance
    solver.Minimize(sum(
        calculate_distance(
            incident.location.lat, incident.location.lng,
            fire_stations[u.station_id].location.lat,
            fire_stations[u.station_id].location.lng,
        ) * x[u.unit_id]
        for u in available_units
    ))

    status = solver.Solve()
    if status == pywraplp.Solver.OPTIMAL:
        return [u for u in available_units if x[u.unit_id].solution_value() > 0.5]

    # Solver didn't find optimal — fall back to greedy
    return _pick_units_greedy(available_units, count_needed, incident, fire_stations)


def _pick_units_greedy(available_units, count_needed, incident, fire_stations):
    """Greedy fallback: pick the count_needed closest units."""
    sorted_units = sorted(
        available_units,
        key=lambda u: calculate_distance(
            incident.location.lat, incident.location.lng,
            fire_stations[u.station_id].location.lat,
            fire_stations[u.station_id].location.lng,
        )
    )
    return sorted_units[:count_needed]


def optimize_dispatch(incident, fleet_units, fire_stations):
    """
    Main dispatch function.
    Returns list of dispatch detail dicts with route shapes.
    """
    dispatched_details = []

    for unit_type, count_needed in incident.required_units.items():
        available_units = [
            u for u in fleet_units.values()
            if u.type == unit_type and u.status == "Available"
        ]

        if len(available_units) < count_needed:
            # Take however many ARE available rather than skipping entirely
            if not available_units:
                continue
            count_needed = len(available_units)

        # Pick optimal units
        if HAS_ORTOOLS and count_needed > 1:
            chosen = _pick_units_ortools(available_units, count_needed, incident, fire_stations)
        else:
            chosen = _pick_units_greedy(available_units, count_needed, incident, fire_stations)

        for u in chosen:
            # FIX: mark as Dispatched so the unit isn't double-assigned
            u.status = "Dispatched"

            station = fire_stations[u.station_id]
            dist_miles, route_latlng = _get_route(
                station.location.lat, station.location.lng,
                incident.location.lat, incident.location.lng,
            )

            dispatched_details.append({
                "unit_id":      u.unit_id,
                "station_name": station.name,
                "distance":     dist_miles,
                "route_shape":  route_latlng,
            })

    return dispatched_details
