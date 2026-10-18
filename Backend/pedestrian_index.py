"""查詢本地三維行人道路網（3DPN）SQLite 索引。

資料來源：地政總署 3D Pedestrian Network（CSDI 開放數據）。
本模組提供：
- query_pedestrian_segments(bbox)：bbox 內全部行人路段
- query_pedestrian_structures(bbox)：行人天橋／行人隧道線幾何（供 DEM 結構修正，較 OSM 優先）
- fetch_pedestrian_facilities(bbox)：升降機／樓梯／斜道等點位設施（較 OSM 優先）
- merge_pedestrian_priority(osm_facilities, pdpn_facilities)：3DPN 優先合併，去除 25 米內重複的 OSM 障礙
- analyze_route_wheelchair(coords, bbox)：路線與 3DPN 匹配，估算輪椅可通行比例與障礙
"""
import json
import logging
import math
import sqlite3
from pathlib import Path

from pyproj import Transformer
from shapely import from_wkb, LineString, Point, STRtree
from shapely.ops import transform as shapely_transform

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PEDESTRIAN_INDEX_PATH = BACKEND_DIR / "data" / "pedestrian-network.sqlite3"

# 代碼對照（來源：LandsD 3DPN 資料字典 coded value domains）
FEATURE_TYPE_LABELS = {
    1: "行人路", 2: "行人天橋", 4: "行人隧道", 5: "服務通道", 6: "交通島",
    7: "車輛出入口", 8: "扶手電梯", 9: "自動行人道", 10: "升降機", 11: "斜道",
    12: "樓梯", 13: "輪椅升降台", 14: "其他",
    15: "港鐵行人路", 16: "港鐵扶手電梯", 17: "港鐵自動行人道", 18: "港鐵升降機",
    19: "港鐵斜道", 20: "港鐵輪椅升降台", 21: "港鐵樓梯", 22: "港鐵其他",
    23: "公園通道", 24: "行人徑", 25: "鄉村通道", 26: "山徑",
    30: "訊號燈過路處", 31: "斑馬線", 32: "警示過路處", 33: "其他過路處",
}

# 3DPN FeatureType → 前端設施類型
FACILITY_TYPE_MAP = {
    10: "elevator", 18: "elevator",
    12: "steps", 21: "steps",
    11: "ramp", 19: "ramp",
    8: "escalator", 16: "escalator",
    9: "travelator", 17: "travelator",
    13: "stairlift", 20: "stairlift",
    30: "crossing", 31: "crossing", 32: "crossing", 33: "crossing",
}

# 結構修正用：行人天橋／行人隧道
STRUCTURE_FEATURE_TYPES = {2: "bridge", 4: "tunnel"}

STAIRCASE_TYPES = {12, 21}

_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
_to_wgs = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)

PEDESTRIAN_ROUTE_TYPES = {
    1, 2, 4, 5, 6, 8, 9, 10, 11, 12, 13,
    15, 16, 17, 18, 19, 20, 21,
    23, 24, 25, 26, 30, 31, 32, 33,
}


def _row_to_segment(row):
    (segment_id, route_id, feature_type, location, crossing_feature, weather_proof,
     wheelchair_barrier, wheelchair_access, gradient, street_tc, street_en, geom_wkb) = row
    geometry = from_wkb(geom_wkb)
    return {
        "id": segment_id,
        "route_id": route_id,
        "feature_type": feature_type,
        "type_label": FEATURE_TYPE_LABELS.get(feature_type, "其他"),
        "location": location,
        "crossing_feature": crossing_feature,
        "weather_proof": weather_proof,
        "wheelchair_barrier": wheelchair_barrier,
        "wheelchair_access": wheelchair_access,
        "gradient": gradient,
        "street_name_tc": street_tc,
        "street_name_en": street_en,
        "geometry": geometry,
    }


def query_pedestrian_segments(bbox, index_path=DEFAULT_PEDESTRIAN_INDEX_PATH):
    south, west, north, east = bbox
    index_path = Path(index_path).resolve()
    if not index_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT s.id, s.route_id, s.feature_type, s.location, s.crossing_feature,
                       s.weather_proof, s.wheelchair_barrier, s.wheelchair_access,
                       s.gradient, s.street_name_tc, s.street_name_en, s.geom_wkb
                FROM segments_rtree AS spatial
                JOIN segments AS s ON s.id = spatial.id
                WHERE spatial.min_longitude <= ? AND spatial.max_longitude >= ?
                  AND spatial.min_latitude <= ? AND spatial.max_latitude >= ?
                """,
                (east, west, north, south),
            ).fetchall()
    except sqlite3.Error:
        logger.exception("Pedestrian index query failed")
        return []
    return [_row_to_segment(row) for row in rows]


def validate_route_pedestrian_network(coords, max_link_m=5.0, sample_distance_m=5.0,
                                      index_path=DEFAULT_PEDESTRIAN_INDEX_PATH):
    """Fail closed unless every route sample matches an allowed 3DPN pedestrian segment."""
    if len(coords) < 2 or sample_distance_m <= 0 or max_link_m < 0:
        return {
            "verified": False,
            "match_ratio": 0.0,
            "matched_samples": 0,
            "total_samples": 0,
            "max_distance_m": None,
            "max_link_m": max_link_m,
        }

    metric_coords = [_to_metric.transform(longitude, latitude) for longitude, latitude in coords]
    route_line = LineString(metric_coords)
    if route_line.is_empty or route_line.length == 0:
        return {
            "verified": False,
            "match_ratio": 0.0,
            "matched_samples": 0,
            "total_samples": 0,
            "max_distance_m": None,
            "max_link_m": max_link_m,
        }

    min_x, min_y, max_x, max_y = route_line.bounds
    buffer_m = max_link_m + 5.0
    bbox_corners = [
        _to_wgs.transform(min_x - buffer_m, min_y - buffer_m),
        _to_wgs.transform(min_x - buffer_m, max_y + buffer_m),
        _to_wgs.transform(max_x + buffer_m, min_y - buffer_m),
        _to_wgs.transform(max_x + buffer_m, max_y + buffer_m),
    ]
    bbox = (
        min(latitude for _, latitude in bbox_corners),
        min(longitude for longitude, _ in bbox_corners),
        max(latitude for _, latitude in bbox_corners),
        max(longitude for longitude, _ in bbox_corners),
    )
    pedestrian_segments = [
        segment for segment in query_pedestrian_segments(bbox, index_path)
        if segment["feature_type"] in PEDESTRIAN_ROUTE_TYPES
    ]
    total_samples = max(2, math.ceil(route_line.length / sample_distance_m) + 1)
    sample_points = [
        route_line.interpolate(route_line.length * index / (total_samples - 1))
        for index in range(total_samples)
    ]
    if not pedestrian_segments:
        return {
            "verified": False,
            "match_ratio": 0.0,
            "matched_samples": 0,
            "total_samples": total_samples,
            "max_distance_m": None,
            "max_link_m": max_link_m,
        }

    metric_geometries = [
        shapely_transform(_to_metric.transform, segment["geometry"])
        for segment in pedestrian_segments
    ]
    tree = STRtree(metric_geometries)
    distances = []
    for point in sample_points:
        nearest_index = int(tree.nearest(point))
        distances.append(metric_geometries[nearest_index].distance(point))

    matched_samples = sum(distance <= max_link_m for distance in distances)
    max_distance = max(distances)
    return {
        "verified": matched_samples == total_samples,
        "match_ratio": round(matched_samples / total_samples, 3),
        "matched_samples": matched_samples,
        "total_samples": total_samples,
        "max_distance_m": round(max_distance, 2),
        "max_link_m": max_link_m,
    }


def _geometry_coord_lines(geometry):
    """把 (Multi)LineString 拆成 [[(lat, lon), ...], ...] 的線列表。"""
    if geometry.geom_type == "LineString":
        return [[(lat, lon) for lon, lat in geometry.coords]]
    if geometry.geom_type == "MultiLineString":
        return [[(lat, lon) for lon, lat in part.coords] for part in geometry.geoms]
    return []


def query_pedestrian_structures(bbox, index_path=DEFAULT_PEDESTRIAN_INDEX_PATH):
    """行人天橋／行人隧道線幾何，格式與 OSM structure_ways 相同。"""
    structures = []
    for segment in query_pedestrian_segments(bbox, index_path):
        structure_type = STRUCTURE_FEATURE_TYPES.get(segment["feature_type"])
        if structure_type is None:
            continue
        for coords in _geometry_coord_lines(segment["geometry"]):
            if len(coords) >= 2:
                structures.append({
                    "structure_type": structure_type,
                    "name": segment["street_name_tc"] or segment["type_label"],
                    "coords": coords,
                })
    return structures


def fetch_pedestrian_facilities(bbox, index_path=DEFAULT_PEDESTRIAN_INDEX_PATH):
    """把 3DPN 特殊路段（升降機／樓梯／斜道／扶手電梯／過路處等）轉成點位設施。"""
    facilities = []
    for segment in query_pedestrian_segments(bbox, index_path):
        facility_type = FACILITY_TYPE_MAP.get(segment["feature_type"])
        if facility_type is None:
            continue
        point = segment["geometry"].representative_point()
        street = segment["street_name_tc"]
        name = f"{street}{segment['type_label']}" if street else segment["type_label"]
        facilities.append({
            "latitude": point.y,
            "longitude": point.x,
            "name": name,
            "source": "3dpn",
            "status": "",
            "type": facility_type,
            "gradient": segment["gradient"],
            "wheelchair_access": segment["wheelchair_access"],
        })
    return facilities


def _distance_m(lat1, lon1, lat2, lon2):
    x1, y1 = _to_metric.transform(lon1, lat1)
    x2, y2 = _to_metric.transform(lon2, lat2)
    return math.hypot(x2 - x1, y2 - y1)


_DEDUPE_GROUPS = {
    "elevator": {"elevator", "lift"},
    "steps": {"step", "steps", "stair", "stairs"},
    "ramp": {"ramp"},
}


def merge_pedestrian_priority(osm_facilities, pdpn_facilities, radius_m=25.0):
    """3DPN 設施優先；OSM 障礙若在 radius_m 內有同類 3DPN 設施則剔除。"""
    pdpn_points = {"elevator": [], "steps": [], "ramp": []}
    for facility in pdpn_facilities:
        group = facility.get("type")
        if group in pdpn_points:
            pdpn_points[group].append((facility["latitude"], facility["longitude"]))

    kept = []
    for facility in osm_facilities:
        facility_type = str(facility.get("type", "")).lower()
        target_group = next((g for g, types in _DEDUPE_GROUPS.items() if facility_type in types), None)
        if target_group is None:
            kept.append(facility)
            continue
        latitude, longitude = facility.get("latitude"), facility.get("longitude")
        duplicated = any(
            _distance_m(latitude, longitude, plat, plon) <= radius_m
            for plat, plon in pdpn_points[target_group]
        )
        if not duplicated:
            kept.append(facility)

    merged = list(pdpn_facilities) + kept
    logger.debug(
        "3DPN merge: pdpn=%d osm_in=%d osm_kept=%d",
        len(pdpn_facilities), len(osm_facilities), len(kept),
    )
    return merged


def analyze_route_wheelchair(coords, bbox, max_link_m=15.0, index_path=DEFAULT_PEDESTRIAN_INDEX_PATH):
    """把路線點匹配到最近的 3DPN 路段，估算無障礙路段比例與障礙數。

    注意：3DPN 的 WheelchairAccess=True 只佔全網約 1%（連升降機亦多數標 False），
    屬稀疏調查值，不宜直接用作「可通行」指標。本函數改以「非樓梯且無障礙物」
    計算 barrier_free_ratio，較貼近實際可通行狀況。

    回傳 None 表示索引缺失或匹配率過低（不足 30%），避免誤導。
    """
    segments = query_pedestrian_segments(bbox, index_path)
    if not segments or not coords:
        return None

    from shapely.ops import transform as shp_transform

    geometries = []
    attributes = []
    for segment in segments:
        try:
            metric_geometry = shp_transform(_to_metric.transform, segment["geometry"])
        except Exception:
            continue
        geometries.append(metric_geometry)
        attributes.append(segment)
    if not geometries:
        return None

    tree = STRtree(geometries)
    matched = 0
    passable = 0
    staircase = 0
    barrier = 0
    for lon, lat in coords:
        x, y = _to_metric.transform(lon, lat)
        point = Point(x, y)
        nearest_index = tree.nearest(point)
        if nearest_index is None:
            continue
        geometry = geometries[int(nearest_index)]
        if geometry.distance(point) > max_link_m:
            continue
        matched += 1
        segment = attributes[int(nearest_index)]
        is_staircase = segment["feature_type"] in STAIRCASE_TYPES
        has_barrier = segment["wheelchair_barrier"] == 1
        if is_staircase:
            staircase += 1
        if has_barrier:
            barrier += 1
        if not is_staircase and not has_barrier:
            passable += 1

    matched_ratio = matched / len(coords)
    if matched_ratio < 0.3:
        return None
    return {
        "matched_ratio": round(matched_ratio, 3),
        "barrier_free_ratio": round(passable / matched, 3) if matched else None,
        "staircase_points": staircase,
        "barrier_points": barrier,
        "matched_points": matched,
        "total_points": len(coords),
    }


# 更新記錄：2026-10-17 新增 3DPN 查詢模組：bbox 查詢、結構提取、設施轉換、3DPN 優先合併及輪椅可通行分析。
