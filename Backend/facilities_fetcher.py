import requests, logging
from config import BARRIER_FREE_API_BASE, BARRIER_FREE_API_KEY, OVERPASS_URL, REQUEST_TIMEOUT
logger = logging.getLogger(__name__)

def fetch_barrier_free(bbox):
    url = f"{BARRIER_FREE_API_BASE}/locations"
    headers = {"Authorization": f"Bearer {BARRIER_FREE_API_KEY}"}
    params = {"bbox": ",".join(map(str,bbox)), "facilityTypes":"elevator,toilet,ramp"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception("Barrier Free error")
        return {}

def fetch_overpass(bbox):
    s,w,n,e = bbox
    q = f"""
    [out:json][timeout:25];
    (
      node["elevator"="yes"]({s},{w},{n},{e});
      node["amenity"="toilets"]({s},{w},{n},{e});
    );
    out body;
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
    except Exception as e:
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
        if el.get("type")!="node": continue
        lat, lon = el.get("lat"), el.get("lon")
        tags = el.get("tags",{})
        if tags.get("elevator")=="yes":
            ftype="elevator"
        elif tags.get("amenity")=="toilets":
            ftype="toilet"
        else: ftype="facility"
        facs.append({"type":ftype,"name":tags.get("name",""),
                     "longitude":lon,"latitude":lat,
                     "source":"osm","status":tags.get("status","")})
    return facs
