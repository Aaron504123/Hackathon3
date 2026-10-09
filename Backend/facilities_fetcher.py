import requests, logging
from collections import Counter
from config import BARRIER_FREE_API_BASE, BARRIER_FREE_API_KEY, OVERPASS_URL, REQUEST_TIMEOUT
from osm_index import DEFAULT_INDEX_PATH, DEFAULT_PBF_PATH, is_index_current, query_osm_index
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def _determine_facility_type(tags, fallback="facility"):
    if tags.get("elevator") == "yes":
        return "elevator"
    if tags.get("highway") == "steps":
        return "steps"
    if tags.get("ramp") == "yes":
        return "ramp"
    if tags.get("wheelchair") == "no":
        return "obstacle"
    return fallback


def _is_barrier_free_managed_osm_feature(tags):
    amenity = str(tags.get("amenity", "")).lower()
    return (
        amenity in {"toilets", "parking", "parking_space"}
        or bool(tags.get("parking"))
        or bool(tags.get("parking_space"))
        or any(key.startswith("parking:") for key in tags)
        or bool(tags.get("capacity:disabled"))
    )


def fetch_barrier_free(bbox):
    if not BARRIER_FREE_API_KEY or BARRIER_FREE_API_KEY.strip() == "YOUR_BARRIER_FREE_API_KEY":
        logger.warning("BARRIER_FREE_API_KEY is not configured; skipping Barrier Free API")
        return {}

    url = f"{BARRIER_FREE_API_BASE}/items"
    headers = {"Authorization": f"Bearer {BARRIER_FREE_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        items = r.json().get("items", [])
        if not isinstance(items, list):
            logger.warning("Barrier Free API returned an invalid items list")
            return {}

        received_categories = Counter(str((item.get("item_category") or {}).get("id")) for item in items)
        south, west, north, east = bbox
        nearby_items = []
        for item in items:
            category_id = (item.get("item_category") or {}).get("id")
            if category_id not in {3, 4}:
                continue
            try:
                latitude = float(item.get("map_lat"))
                longitude = float(item.get("map_lng"))
            except (TypeError, ValueError):
                continue
            if south <= latitude <= north and west <= longitude <= east:
                nearby_items.append(item)

        logger.debug(
            "BarrierFreeMap items received=%d categories=%s bbox_matches=%d",
            len(items), received_categories, len(nearby_items),
        )
        return {"items": nearby_items}
    except requests.HTTPError as error:
        status_code = error.response.status_code if error.response is not None else "unknown"
        logger.warning("Barrier Free API request failed (HTTP %s); continuing without this source", status_code)
        return {}
    except Exception:
        logger.exception("Barrier Free error")
        return {}


def fetch_overpass(bbox):
    if is_index_current(DEFAULT_INDEX_PATH, DEFAULT_PBF_PATH):
        try:
            elements = query_osm_index(bbox, DEFAULT_INDEX_PATH)
            logger.debug("Local OSM index matches bbox; elements=%d", len(elements))
            return {"elements": elements}
        except Exception:
            logger.exception("Local OSM index query failed; falling back to Overpass")
    else:
        logger.warning(
            "Local OSM index is missing or stale; using Overpass. Build it with: "
            "python build_osm_index.py"
        )

    s,w,n,e = bbox
    q = f"""
    [out:json][timeout:25];
    (
      node["elevator"="yes"]({s},{w},{n},{e});
      node["highway"="steps"]({s},{w},{n},{e});
      way["highway"="steps"]({s},{w},{n},{e});
      node["ramp"="yes"]({s},{w},{n},{e});
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
        payload = r.json()
        logger.debug("Overpass elements received=%d", len(payload.get("elements", [])))
        return payload
    except Exception:
        logger.exception("Overpass error")
        return {}


def normalize(barrier_json, overpass_json):
    facs = []
    excluded_types = Counter()
    # Barrier Free
    for item in barrier_json.get("items", []):
        category = item.get("item_category") or {}
        category_id = category.get("id")
        if category_id == 3:
            facility_type = "toilet"
        elif category_id == 4:
            facility_type = "parking"
        else:
            continue

        latitude, longitude = item.get("map_lat"), item.get("map_lng")
        if latitude is None or longitude is None:
            continue
        access_status = item.get("access_status") or {}
        facs.append({
            "type": facility_type,
            "name": item.get("item_name_zh") or item.get("item_name_en") or category.get("category_name_zh", facility_type),
            "longitude": longitude,
            "latitude": latitude,
            "source": "barrierfree",
            "status": access_status.get("status_name_zh") or access_status.get("status_name_en", ""),
            "updated_at": item.get("updated_at", ""),
            "url": item.get("url", ""),
        })

    # Overpass
    for el in overpass_json.get("elements", []):
        lat, lon = el.get("lat"), el.get("lon")
        tags = el.get("tags", {})
        if _is_barrier_free_managed_osm_feature(tags):
            continue
        if el.get("type") == "way":
            lat = (el.get("center") or {}).get("lat")
            lon = (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        ftype = _determine_facility_type(tags, fallback="facility")
        if ftype in {"facility", "obstacle"}:
            excluded_types[ftype] += 1
            continue
        facs.append({"type":ftype,"name":tags.get("name") or ftype,
                     "longitude":lon,"latitude":lat,
                     "source":"osm","status":tags.get("status","")})

    normalized_types = Counter(facility["type"] for facility in facs)
    logger.debug(
        "Facilities normalized by type=%s; excluded_unclassified=%d excluded_generic_obstacles=%d",
        normalized_types,
        excluded_types["facility"],
        excluded_types["obstacle"],
    )
    return facs


# 更新記錄：2026-10-09 排除未分類 facility/obstacle，並加入 API、Overpass 和標準化數量的 debug 記錄。
# 更新記錄：2026-10-09 優先使用本地香港 PBF SQLite 索引查詢 OSM 設施，索引缺失/過期時才 fallback Overpass。
