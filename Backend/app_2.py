from flask import Flask, request, jsonify
import requests, logging
from config import MAPBOX_TOKEN, SAMPLE_DISTANCE_M
from dem_processing_mixed import process_dem_route_mixed
from facilities_fetcher import fetch_barrier_free, fetch_overpass, normalize

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

MAPBOX_URL = "https://api.mapbox.com/directions/v5/mapbox/walking"


def build_fallback_route(start, end, steps=25):
    """當 Mapbox Directions API 失敗時，生成一條直線路徑作為替代"""
    lon1, lat1 = start
    lon2, lat2 = end
    coords = []
    for i in range(steps + 1):
        t = i / steps
        lon = lon1 + (lon2 - lon1) * t
        lat = lat1 + (lat2 - lat1) * t
        coords.append([lon, lat])
    return coords


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/route", methods=["POST"])
def route():
    data = request.get_json() or {}
    start, end = data.get("start"), data.get("end")
    if not start or not end:
        return jsonify({"error": "start/end required"}), 400

    # 1. Mapbox Directions API (有 fallback)
    try:
        url = f"{MAPBOX_URL}/{start[0]},{start[1]};{end[0]},{end[1]}"
        params = {
            "geometries": "geojson",
            "overview": "full",
            "access_token": MAPBOX_TOKEN
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        payload = r.json()
        routes = payload.get("routes") or []
        coords = routes[0]["geometry"]["coordinates"] if routes else build_fallback_route(start, end)
    except Exception:
        logging.exception("Mapbox request failed, using fallback route")
        coords = build_fallback_route(start, end)

    # 2. DEM slope (混合模式)
    slopes = process_dem_route_mixed(coords, sample_distance=SAMPLE_DISTANCE_M)

    # 3. Facilities (Barrier Free + Overpass)
    lons, lats = [c[0] for c in coords], [c[1] for c in coords]
    pad = 0.0005
    bbox = [min(lats) - pad, min(lons) - pad, max(lats) + pad, max(lons) + pad]
    bf, op = fetch_barrier_free(bbox), fetch_overpass(bbox)
    facilities = normalize(bf, op)

    # 4. 回傳結果
    return jsonify({
        "route": {"type": "LineString", "coordinates": coords},
        "slopes": slopes,
        "facilities": facilities,
        "summary": {
            "max_slope_deg": max([abs(s) for s in slopes], default=None),
            "facility_count": len(facilities)
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
