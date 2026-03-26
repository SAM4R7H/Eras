"""AI Engine for evaluating incident priority and resource requirements."""

import random
from datetime import datetime
from typing import Dict, Tuple, List, Optional


# Priority score ranges by incident type (min, max)
INCIDENT_PRIORITY_RANGES = {
    "Fire": (75, 100),
    "Medical": (40, 95),
    "Traffic Collision": (60, 90),
    "Hazmat": (90, 100),
    "Rescue": (70, 95),
}

# Default unit requirements by incident type
DEFAULT_UNIT_REQUIREMENTS = {
    "Fire": {"Engine": 2, "Ladder": 1},
    "Medical": {"Ambulance": 1},
    "Traffic Collision": {"Engine": 1, "Ambulance": 1},
    "Hazmat": {"Engine": 2, "Ladder": 1, "Hazmat": 1},
    "Rescue": {"Ladder": 1, "Ambulance": 1},
}

# Rush hour periods (24-hour format)
RUSH_HOURS = {8, 9, 17, 18}
RUSH_HOUR_PRIORITY_BONUS = 5
MAX_PRIORITY = 100

# Medical incident capability requirements
# P1 (priority >= 80) requires ALS, P2/P3 can use BLS
MEDICAL_CAPABILITY_THRESHOLDS = {
    "ALS_required": 80,  # Priority >= 80 needs Advanced Life Support
    "ALS_preferred": 60,  # Priority >= 60 prefers ALS but BLS acceptable
}


def _get_medical_units(priority: int) -> Dict[str, int]:
    """Determine required units for medical incidents based on priority."""
    if priority > 80:
        return {"Ambulance": 1, "Engine": 1}
    return {"Ambulance": 1}


def get_capability_requirement(incident_type: str, priority: int) -> Optional[str]:
    """
    Determine the required medical capability for an incident.
    
    Args:
        incident_type: Type of incident
        priority: Priority score (0-100)
    
    Returns:
        'ALS' if ALS required, 'ALS_preferred' if preferred, None otherwise
    """
    if incident_type != "Medical":
        return None
    
    if priority >= MEDICAL_CAPABILITY_THRESHOLDS["ALS_required"]:
        return "ALS"
    elif priority >= MEDICAL_CAPABILITY_THRESHOLDS["ALS_preferred"]:
        return "ALS_preferred"
    return None


def evaluate_incident(incident_type: str, reported_time: datetime) -> Tuple[int, Dict[str, int]]:
    """
    Evaluate an incident to determine priority score and required units.

    Args:
        incident_type: Type of incident (e.g., "Fire", "Medical")
        reported_time: When the incident was reported

    Returns:
        Tuple of (priority_score, required_units_dict)
    """
    # Get base priority range for incident type
    priority_range = INCIDENT_PRIORITY_RANGES.get(incident_type, (40, 60))
    priority = random.randint(*priority_range)

    # Get required units for incident type
    required_units = DEFAULT_UNIT_REQUIREMENTS.get(
        incident_type,
        {"Engine": 1}  # Default fallback
    ).copy()

    # Special handling for Medical incidents (priority-dependent)
    if incident_type == "Medical":
        required_units = _get_medical_units(priority)

    # Apply rush hour modifier
    if reported_time.hour in RUSH_HOURS:
        priority = min(MAX_PRIORITY, priority + RUSH_HOUR_PRIORITY_BONUS)

    return priority, required_units
