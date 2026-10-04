from flask import Flask, request, jsonify
import logging
import math
import requests
import polyline
from concurrent.futures import ThreadPoolExecutor
from geographiclib.geodesic import Geodesic

from config import MAPBOX_TOKEN, ORS_API_KEY, ORS_API_BASE, ORS_PROFILE, SAMPLE_DISTANCE_M
from dem_processing import sample_elevations, smooth_elevations, compute_slopes
from facilities_fetcher import fetch_barrier_free, fetch_overpass, normalize

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

MAPBOX_URL = "https://api.mapbox.com/directions/v5/mapbox/walking"
ORS_URL = f"{ORS_API_BASE}/v2/directions/{ORS_PROFILE}/json"
FACILITY_ROUTE_RADIUS_METERS = 40


def build_fallback_route(start, end, steps=25):
    """When Mapbox Directions API fails, return a straight-line fallback route."""
    lon1, lat1 = start
    lon2, lat2 = end
    coords = []
    for i in range(steps + 1):
        t = i / steps
        lon = lon1 + (lon2 - lon1) * t
        lat = lat1 + (lat2 - lat1) * t
        coords.append([lon, lat])
    return coords


def _distance_meters(coord_a, coord_b):
    lon1, lat1 = coord_a
    lon2, lat2 = coord_b
    return Geodesic.WGS84.Inverse(lat1, lon1, lat2, lon2)["s12"]


def _distance_to_route(route_coords, point):
    if not route_coords:
        return float("inf")
    point_lon, point_lat = point
    if len(route_coords) == 1:
        return _distance_meters(point, route_coords[0])

    longitude_scale = 111320 * math.cos(math.radians(point_lat))
    latitude_scale = 110574
    point_segments = []
    for coordinate in route_coords:
        point_segments.append((
            (coordinate[0] - point_lon) * longitude_scale,
            (coordinate[1] - point_lat) * latitude_scale,
        ))

    minimum_distance = float("inf")
    for index in range(len(point_segments) - 1):
        start_x, start_y = point_segments[index]
        end_x, end_y = point_segments[index + 1]
        segment_x = end_x - start_x
        segment_y = end_y - start_y
        segment_length_squared = segment_x ** 2 + segment_y ** 2
        if segment_length_squared:
            fraction = max(0, min(1, -(start_x * segment_x + start_y * segment_y) / segment_length_squared))
        else:
            fraction = 0
        closest_x = start_x + fraction * segment_x
        closest_y = start_y + fraction * segment_y
        distance = math.hypot(closest_x, closest_y)
        minimum_distance = min(minimum_distance, distance)

    return minimum_distance


def _filter_facilities_near_route(facilities, route_coords):
    nearby_facilities = []
    for facility in facilities:
        longitude = facility.get("longitude")
        latitude = facility.get("latitude")
        if longitude is None or latitude is None:
            continue
        if _distance_to_route(route_coords, [longitude, latitude]) <= FACILITY_ROUTE_RADIUS_METERS:
            nearby_facilities.append(facility)
    return nearby_facilities


def normalize_route_geometry(route):
    geometry = route.get("geometry", {})
    if isinstance(geometry, str):
        coords = polyline.decode(geometry, geojson=True)
        route["geometry"] = {"type": "LineString", "coordinates": coords}
        return coords
    if isinstance(geometry, dict):
        return geometry.get("coordinates", [])
    if isinstance(geometry, list):
        return geometry
    return []


def choose_preferred_route(routes, obstacles):
    if not routes:
        return None

    scored_routes = []
    for index, route in enumerate(routes):
        route_coords = normalize_route_geometry(route)
        summary = route.get("summary", {})
        distance = summary.get("distance") or len(route_coords) * 100
        score = float(distance)
        has_steps = False
        has_elevator = False

        for obstacle in obstacles:
            obstacle_point = [obstacle.get("longitude"), obstacle.get("latitude")]
            proximity = _distance_to_route(route_coords, obstacle_point)
            obstacle_type = str(obstacle.get("type", "")).lower()
            if proximity <= 200 and ("elevator" in obstacle_type or "lift" in obstacle_type):
                score += 10000
                has_elevator = True
            elif proximity <= 120 and ("step" in obstacle_type or "stair" in obstacle_type):
                score -= 1000
                has_steps = True

        if has_elevator and not has_steps:
            score += 5000
        elif has_steps:
            score -= 2000

        scored_routes.append((score, index, route))

    scored_routes.sort(key=lambda item: item[0], reverse=True)
    return scored_routes[0][2]


def classify_route_obstacles(obstacles):
    obstacle_types = []
    for obstacle in obstacles:
        obstacle_type = str(obstacle.get("type", "")).lower()
        if obstacle_type and obstacle_type not in obstacle_types:
            obstacle_types.append(obstacle_type)

    return {
        "has_step_obstacle": any("step" in item or "stair" in item for item in obstacle_types),
        "has_elevator_access": any("elevator" in item or "lift" in item for item in obstacle_types),
        "obstacle_types": obstacle_types,
        "count": len(obstacle_types),
    }


def query_ors_routes(start, end):
    body = {
        "coordinates": [[start[0], start[1]], [end[0], end[1]]],
        "attributes": ["percentage"],
        "extra_info": [
            "steepness",
            "suitability",
            "surface",
            "waycategory",
            "waytype",
            "traildifficulty",
            "roadaccessrestrictions",
            "shadow",
        ],
        "instructions": "true",
        "instructions_format": "text",
        "language": "zh",
        "options": {
            "avoid_features": ["steps"],
            "profile_params": {
                "restrictions": {
                    "maximum_incline": 10
                }
            }
        },
        "preference": "recommended",
        "units": "m",
    }
    headers = {
        "Authorization": ORS_API_KEY,
        "Accept": "application/json, application/geo+json, application/gpx+xml, img/png; charset=utf-8",
        "Content-Type": "application/json; charset=utf-8",
    }
    response = requests.post(ORS_URL, json=body, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


def fetch_facilities_nearby(bbox):
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            barrier_free_future = executor.submit(fetch_barrier_free, bbox)
            overpass_future = executor.submit(fetch_overpass, bbox)
            bf = barrier_free_future.result()
            op = overpass_future.result()
        return normalize(bf, op)
    except Exception:
        logging.exception("Facility lookup failed; continuing route without facility metadata")
        return []


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/route", methods=["POST"])
def route():
    data = request.get_json() or {}
    start, end = data.get("start"), data.get("end")
    if not start or not end:
        return jsonify({"error": "start/end required"}), 400

    coords = build_fallback_route(start, end)
    route_source = "fallback"
    route_obstacles = []

    try:
        ors_payload = query_ors_routes(start, end)
        routes = ors_payload.get("routes") or []
        if routes:
            lons, lats = [c[0] for c in coords], [c[1] for c in coords]
            pad = 0.0012
            bbox = [min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad]
            obstacles = fetch_facilities_nearby(bbox)
            selected_route = choose_preferred_route(routes, obstacles)
            if selected_route is not None:
                coords = normalize_route_geometry(selected_route)
                route_source = "openrouteservice"
                route_obstacles = obstacles
            else:
                raise ValueError("No valid ORS route selected")
        else:
            raise ValueError("No ORS routes returned")
    except Exception:
        logging.exception("ORS route failed, using fallback route")
        route_source = "fallback"
        coords = build_fallback_route(start, end)

    # DEM slope calculation
    samples = smooth_elevations(
        sample_elevations(coords, sample_distance_m=SAMPLE_DISTANCE_M)
    )
    segments = compute_slopes(samples)
    elevations = [float(elevation) for _, _, elevation in samples if elevation is not None]
    slopes = [float(segment["slope_deg"]) for segment in segments if segment.get("slope_deg") is not None]

    # Facility lookup
    lons, lats = [c[0] for c in coords], [c[1] for c in coords]
    pad = 0.0005
    bbox = [min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad]
    facilities = _filter_facilities_near_route(fetch_facilities_nearby(bbox), coords)

    route_facility_summary = classify_route_obstacles(facilities)
    max_slope = max([abs(s["slope_deg"]) for s in segments if s["slope_deg"] is not None], default=None)

    route_warning = None
    if route_source == "fallback":
        route_warning = "Mapbox/ORS 路由不可用，使用直線備援；此路徑不代表可行走道路。"
    elif route_facility_summary["has_step_obstacle"]:
        route_warning = "本路線沿線有已標記的樓梯／步階障礙，請以實際路況與設施資料確認。"
    elif route_facility_summary["has_elevator_access"]:
        route_warning = "本路線可接近已標記升降機，較適合作為無障礙路線參考。"

    return jsonify({
        "route": {"type": "LineString", "coordinates": coords},
        "elevations": elevations,
        "slopes": slopes,
        "segments": segments,
        "facilities": facilities,
        "route_obstacles": route_facility_summary,
        "route_source": route_source,
        "warning": route_warning,
        "summary": {
            "max_slope_deg": max_slope,
            "facility_count": len(facilities),
            "has_step_obstacle": route_facility_summary["has_step_obstacle"],
            "has_elevator_access": route_facility_summary["has_elevator_access"],
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
