"""Request validators: coordinate order/range and per-request limits, from config/pricing.yaml."""
from .pricing_calc import load_pricing

# Rough bounding box of India, used only to detect a probable lat/lng swap.
INDIA = {"lat": (6.0, 38.0), "lng": (68.0, 98.0)}


def coord_order(provider: str) -> str:
    return load_pricing()["coord_order"].get(provider, "lat,lng")


def validate_latlng(lat: float, lng: float) -> list[str]:
    """Issues for a coordinate already split into (lat, lng)."""
    issues = []
    if not -90 <= lat <= 90:
        issues.append(f"latitude {lat} is outside [-90, 90]")
    if not -180 <= lng <= 180:
        issues.append(f"longitude {lng} is outside [-180, 180]")
    in_india = INDIA["lat"][0] <= lat <= INDIA["lat"][1] and INDIA["lng"][0] <= lng <= INDIA["lng"][1]
    swapped_in_india = INDIA["lat"][0] <= lng <= INDIA["lat"][1] and INDIA["lng"][0] <= lat <= INDIA["lng"][1]
    if swapped_in_india and not in_india:
        issues.append(f"({lat}, {lng}) looks like longitude,latitude swapped - it only falls in India as ({lng}, {lat})")
    return issues


def validate_pair(text: str, provider: str) -> list[str]:
    """Validate an 'a,b' string using the provider's expected order (Mapbox/OSRM: lng,lat; others: lat,lng)."""
    try:
        a, b = (float(x) for x in text.split(","))
    except ValueError:
        return [f"'{text}' is not a 'number,number' coordinate pair"]
    lat, lng = (b, a) if coord_order(provider) == "lng,lat" else (a, b)
    return validate_latlng(lat, lng)


def _lim(key: str) -> dict:
    return load_pricing()["limits"][key]


def validate_matrix(provider: str, origins: int, destinations: int, *, traffic: bool = False,
                    profile: str = "driving") -> list[str]:
    """Check one Distance Matrix request against published limits."""
    issues, elements = [], origins * destinations
    if provider == "Google Maps Platform":
        L = _lim("google.distance_matrix")
        if origins > L["max_origins"]:
            issues.append(f"{origins} origins > max {L['max_origins']}")
        if destinations > L["max_destinations"]:
            issues.append(f"{destinations} destinations > max {L['max_destinations']}")
        if elements > L["max_elements"]:
            issues.append(f"{elements} elements > max {L['max_elements']} per request")
    elif provider == "Ola Maps":
        L = _lim("ola.distance_matrix")
        if elements > L["max_pairs"]:
            issues.append(f"{origins}x{destinations} = {elements} pairs > max {L['max_pairs']} per request")
    elif provider == "Mapbox":
        L = _lim("mapbox.matrix")
        cap = L["max_coordinates_driving_traffic"] if profile == "driving-traffic" else L["max_coordinates"]
        if origins + destinations > cap and not (origins <= cap and destinations <= cap):
            issues.append(f"coordinates exceed max {cap} for profile {profile}")
        if elements > L["max_elements"]:
            issues.append(f"{elements} elements > max {L['max_elements']}")
    else:
        issues.append(f"no verified matrix limits for {provider}")
    return issues


def validate_waypoints(provider: str, api: str, waypoints: int) -> list[str]:
    """`waypoints` = intermediate stops (excluding origin and destination) except Mapbox, which counts coordinates."""
    if provider == "Google Maps Platform":
        L = _lim("google.directions")
        if waypoints > L["max_waypoints"]:
            return [f"{waypoints} waypoints > max {L['max_waypoints']}"]
    elif provider == "Ola Maps":
        key = "ola.directions_basic" if "basic" in api.lower() else "ola.directions"
        if waypoints > _lim(key)["max_waypoints"]:
            return [f"{waypoints} waypoints > max {_lim(key)['max_waypoints']} for {key}"]
    elif provider == "Mapbox":
        if waypoints > _lim("mapbox.directions")["max_coordinates"]:
            return [f"{waypoints} coordinates > max {_lim('mapbox.directions')['max_coordinates']}"]
    else:
        return [f"no verified waypoint limits for {provider}"]
    return []
