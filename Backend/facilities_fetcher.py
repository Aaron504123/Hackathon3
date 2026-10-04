import requests, logging
from config import BARRIER_FREE_API_BASE, BARRIER_FREE_API_KEY, OVERPASS_URL, REQUEST_TIMEOUT
logger = logging.getLogger(__name__)


def _determine_facility_type(tags, fallback="facility"):
    if tags.get("elevator") == "yes":
        return "elevator"
    if tags.get("highway") == "steps":
        return "steps"
    if tags.get("amenity") == "toilets":
        return "toilet"
    if tags.get("ramp") == "yes":
        return "ramp"
    if tags.get("wheelchair") == "no":
        return "obstacle"
    return fallback


def fetch_barrier_free(bbox):
    url = f"{BARRIER_FREE_API_BASE}/locations"
    headers = {"Authorization": f"Bearer {BARRIER_FREE_API_KEY}"}
    params = {"bbox": ",".join(map(str,bbox)), "facilityTypes":"elevator,toilet,ramp"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("Barrier Free error")
        return {}


def fetch_overpass(bbox):
    s,w,n,e = bbox
    q = f"""
    [out:json][timeout:25];
    (
      node["elevator"="yes"]({s},{w},{n},{e});
      node["amenity"="toilets"]({s},{w},{n},{e});
      node["highway"="steps"]({s},{w},{n},{e});
      way["highway"="steps"]({s},{w},{n},{e});
      node["ramp"="yes"]({s},{w},{n},{e});
      node["wheelchair"="no"]({s},{w},{n},{e});
    );
    out body;
    >;
    out skel qt;
    """
    try:
        r = requests.post(
            OVERPASS_URL,
            data=q.encode('utf-8'),
            headers={"User-Agent": "AccessibleRouteHackathon/1.0", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        logger.exception("Overpass error")
        return {}


def normalize(barrier_json, overpass_json):
    facs = []
    # Barrier Free
    for it in barrier_json.get("facilities", []):
        lat, lon = it.get("latitude"), it.get("longitude")
        if lat and lon:
            facs.append({"type":it.get("facilityType","facility").lower(),
                         "name":it.get("name",""),
                         "longitude":lon,"latitude":lat,
                         "source":"barrierfree","status":it.get("status","")})
    # Overpass
    for el in overpass_json.get("elements", []):
        lat, lon = el.get("lat"), el.get("lon")
        tags = el.get("tags", {})
        if el.get("type") == "way":
            lat = (el.get("center") or {}).get("lat")
            lon = (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        ftype = _determine_facility_type(tags, fallback="facility")
        facs.append({"type":ftype,"name":tags.get("name") or ftype,
                     "longitude":lon,"latitude":lat,
                     "source":"osm","status":tags.get("status","")})
    return facs
