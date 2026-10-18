import csv
import html
import math
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
FACILITIES_CSV = DATA_DIR / "mtr_barrier_free_facilities.csv"
CATEGORY_CSV = DATA_DIR / "mtr_barrier_free_category.csv"
STATIONS_CSV = DATA_DIR / "mtr_lines_and_stations.csv"

_station_lookup_cache = None


def _normalize_name(name):
    return "".join(str(name or "").lower().split())


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _load_station_lookup():
    """載入港鐵無障礙設施 CSV，回傳 {正規化站名: {name_zh, name_en, facilities}}。"""
    global _station_lookup_cache
    if _station_lookup_cache is not None:
        return _station_lookup_cache

    lookup = {}
    try:
        stations = {}
        for row in _read_csv(STATIONS_CSV):
            station_id = (row.get("Station ID") or "").strip()
            if station_id and station_id not in stations:
                stations[station_id] = {
                    "name_zh": (row.get("Chinese Name") or "").strip(),
                    "name_en": (row.get("English Name") or "").strip(),
                }

        categories = {}
        for row in _read_csv(CATEGORY_CSV):
            key = (row.get("Item_Code") or "").strip()
            if key:
                categories[key] = html.unescape((row.get("Facility_Zh") or row.get("Facility_En") or key).strip())

        per_station = {}
        for row in _read_csv(FACILITIES_CSV):
            if (row.get("Value") or "").strip().upper() != "Y":
                continue
            station_no = (row.get("Station_No") or "").strip()
            key = (row.get("Key") or "").strip()
            if not station_no or not key:
                continue
            per_station.setdefault(station_no, []).append({
                "key": key,
                "label": categories.get(key, key),
                "exit": html.unescape((row.get("AJTextZh") or row.get("AJTextEn") or "").strip()),
            })

        for station_no, facilities in per_station.items():
            station = stations.get(station_no)
            if not station:
                continue
            entry = {
                "name_zh": station["name_zh"],
                "name_en": station["name_en"],
                "facilities": facilities,
            }
            for name in (station["name_zh"], station["name_en"]):
                normalized = _normalize_name(name)
                if normalized:
                    lookup[normalized] = entry
    except FileNotFoundError:
        lookup = {}

    _station_lookup_cache = lookup
    return lookup


def match_station(name):
    """以站名查找港鐵官方無障礙設施資料；支援完全吻合及包含式比對。"""
    lookup = _load_station_lookup()
    normalized = _normalize_name(name)
    if not normalized:
        return None
    if normalized in lookup:
        return lookup[normalized]
    for key, entry in lookup.items():
        if key in normalized or normalized in key:
            return entry
    return None


def _haversine_m(lat1, lon1, lat2, lon2):
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def attach_mtr_barrier_free_info(facilities, max_link_m=500.0):
    """把港鐵官方無障礙設施資料附加到 station / station_exit 設施上。

    - type=station 的設施以站名直接比對。
    - type=station_exit 的設施連結到 max_link_m 內最近且已成功比對的車站。
    """
    stations = []
    for facility in facilities:
        if facility.get("type") != "station":
            continue
        info = match_station(facility.get("name"))
        if info:
            facility["mtr_station_name"] = info["name_zh"]
            facility["mtr_barrier_free"] = info["facilities"]
            stations.append(facility)

    if not stations:
        return

    for facility in facilities:
        if facility.get("type") != "station_exit":
            continue
        best = None
        for station in stations:
            distance = _haversine_m(
                facility["latitude"], facility["longitude"],
                station["latitude"], station["longitude"],
            )
            if distance <= max_link_m and (best is None or distance < best[0]):
                best = (distance, station)
        if best:
            facility["mtr_station_name"] = best[1].get("mtr_station_name")
            facility["mtr_barrier_free"] = best[1].get("mtr_barrier_free")


# 更新記錄：2026-10-17 新增港鐵官方無障礙設施 CSV 載入、站名比對及設施連結邏輯。
