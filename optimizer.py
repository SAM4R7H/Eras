import math
import time
import requests

try:
    from ortools.linear_solver import pywraplp
    HAS_ORTOOLS = True
except ImportError:
    HAS_ORTOOLS = False


def calculate_distance(lat1, lon1, lat2, lon2):
    return math.sqrt((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2)


def deg_to_miles(deg):
    return round(deg * 69.0, 2)


def _get_route(lat1, lon1, lat2, lon2):
    osrm_url = (
        f"http://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}"
        f"?overview=full&geometries=geojson"
    )
    try:
        res = requests.get(osrm_url, timeout=4).json()
        route = res["routes"][0]
        dist_miles   = round(route["distance"] / 1609.34, 2)
        duration_s   = round(route["duration"], 1)
        route_latlng = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        return dist_miles, route_latlng, duration_s, True
    except Exception:
        straight_dist = round(calculate_distance(lat1, lon1, lat2, lon2) * 69.0, 2)
        return straight_dist, [[lat1, lon1], [lat2, lon2]], None, False


def _score_candidates(available_units, incident, fire_stations):
    scored = []
    for u in available_units:
        st = fire_stations[u.station_id]
        dist_deg   = calculate_distance(
            incident.location.lat, incident.location.lng,
            st.location.lat, st.location.lng
        )
        dist_miles = deg_to_miles(dist_deg)
        scored.append({
            "unit_id":    u.unit_id,
            "station":    st.name,
            "dist_deg":   round(dist_deg, 5),
            "dist_miles": dist_miles,
            "cost_score": round(dist_deg * 1000, 3),
        })
    scored.sort(key=lambda x: x["dist_deg"])
    return scored


def _pick_units_ortools(available_units, count_needed, incident, fire_stations):
    solver = pywraplp.Solver.CreateSolver("CBC_MIXED_INTEGER_PROGRAMMING")
    if not solver:
        return _pick_units_greedy(available_units, count_needed, incident, fire_stations), "Greedy (solver init failed)"

    x = {u.unit_id: solver.IntVar(0, 1, f"x_{u.unit_id}") for u in available_units}
    solver.Add(sum(x[u.unit_id] for u in available_units) == count_needed)
    solver.Minimize(sum(
        calculate_distance(
            incident.location.lat, incident.location.lng,
            fire_stations[u.station_id].location.lat,
            fire_stations[u.station_id].location.lng,
        ) * x[u.unit_id]
        for u in available_units
    ))

    t0 = time.perf_counter()
    status = solver.Solve()
    solve_ms = round((time.perf_counter() - t0) * 1000, 2)

    if status == pywraplp.Solver.OPTIMAL:
        chosen  = [u for u in available_units if x[u.unit_id].solution_value() > 0.5]
        obj_val = round(solver.Objective().Value() * 1000, 3)
        return chosen, f"OR-Tools CBC  |  obj={obj_val}  |  {solve_ms}ms"

    return _pick_units_greedy(available_units, count_needed, incident, fire_stations), "Greedy (ILP non-optimal)"


def _pick_units_greedy(available_units, count_needed, incident, fire_stations):
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
    dispatched_details = []
    all_calc_steps     = []

    for unit_type, count_needed in incident.required_units.items():
        available_units = [
            u for u in fleet_units.values()
            if u.type == unit_type and u.status == "Available"
        ]

        if not available_units:
            all_calc_steps.append({
                "unit_type": unit_type, "needed": count_needed,
                "available": 0, "candidates": [],
                "solver": "N/A — no units available", "selected": [],
            })
            continue

        if len(available_units) < count_needed:
            count_needed = len(available_units)

        candidates   = _score_candidates(available_units, incident, fire_stations)
        solver_label = "Greedy (n=1)"

        if HAS_ORTOOLS and count_needed > 1:
            chosen, solver_label = _pick_units_ortools(available_units, count_needed, incident, fire_stations)
        else:
            chosen = _pick_units_greedy(available_units, count_needed, incident, fire_stations)
            if not HAS_ORTOOLS:
                solver_label = "Greedy (OR-Tools not installed)"

        chosen_ids = {u.unit_id for u in chosen}
        for c in candidates:
            c["selected"] = c["unit_id"] in chosen_ids

        all_calc_steps.append({
            "unit_type": unit_type, "needed": count_needed,
            "available": len(available_units), "candidates": candidates,
            "solver": solver_label, "selected": list(chosen_ids),
        })

        for u in chosen:
            u.status = "Dispatched"
            station  = fire_stations[u.station_id]
            dist_miles, route_latlng, duration_s, is_road = _get_route(
                station.location.lat, station.location.lng,
                incident.location.lat, incident.location.lng,
            )
            dispatched_details.append({
                "unit_id":       u.unit_id,
                "unit_type":     u.type,
                "station_name":  station.name,
                "distance":      dist_miles,
                "duration_s":    duration_s,
                "route_shape":   route_latlng,
                "is_road_route": is_road,
            })

    return dispatched_details, all_calc_steps
