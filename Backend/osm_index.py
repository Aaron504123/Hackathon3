import json
import logging
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import osmium

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_PBF_PATH = Path(os.getenv("OSM_PBF_PATH", BACKEND_DIR.parent / "hong-kong-261003.osm.pbf"))
DEFAULT_INDEX_PATH = Path(os.getenv("OSM_INDEX_PATH", BACKEND_DIR / "data" / "hong-kong-osm.sqlite3"))


def _create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE facilities (
            id INTEGER PRIMARY KEY,
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            facility_type TEXT NOT NULL,
            name TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            tags_json TEXT NOT NULL,
            UNIQUE (osm_type, osm_id)
        );
        CREATE VIRTUAL TABLE facility_spatial_index USING rtree(
            id, min_longitude, max_longitude, min_latitude, max_latitude
        );
        CREATE TABLE structure_ways (
            id INTEGER PRIMARY KEY,
            osm_id INTEGER NOT NULL UNIQUE,
            structure_type TEXT NOT NULL,
            name TEXT NOT NULL,
            coords_json TEXT NOT NULL,
            tags_json TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE structure_way_spatial_index USING rtree(
            id, min_longitude, max_longitude, min_latitude, max_latitude
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX facilities_type_index ON facilities(facility_type);
        """
    )


def _facility_type(tags):
    railway = str(tags.get("railway", "")).lower()
    public_transport = str(tags.get("public_transport", "")).lower()

    if public_transport == "station_exit":
        return "station_exit"
    if railway in {"subway_entrance", "station_entrance"}:
        return "station_exit"
    if tags.get("elevator") == "yes" or tags.get("highway") == "elevator":
        return "elevator"
    if tags.get("highway") == "steps":
        return "steps"
    if tags.get("ramp") == "yes":
        return "ramp"
    return None


def _structure_type(tags):
    bridge = str(tags.get("bridge", "")).lower()
    tunnel = str(tags.get("tunnel", "")).lower()
    if bridge and bridge != "no":
        return "bridge"
    if tunnel and tunnel != "no":
        return "tunnel"
    return None


class _FacilityHandler(osmium.SimpleHandler):
    def __init__(self, connection, batch_size=1000):
        super().__init__()
        self.connection = connection
        self.batch_size = batch_size
        self.pending = []
        self.pending_structures = []
        self.type_counts = Counter()

    def _add(self, osm_type, osm_id, facility_type, tags, latitude, longitude):
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            return
        tag_values = {tag.k: tag.v for tag in tags}
        name = tag_values.get("name") or tag_values.get("name:zh") or facility_type
        self.pending.append((
            osm_type,
            osm_id,
            facility_type,
            name,
            latitude,
            longitude,
            json.dumps(tag_values, ensure_ascii=False),
        ))
        self.type_counts[facility_type] += 1
        if len(self.pending) >= self.batch_size:
            self.flush()

    def node(self, node):
        facility_type = _facility_type(node.tags)
        if facility_type is None or not node.location.valid():
            return
        self._add("node", node.id, facility_type, node.tags, node.location.lat, node.location.lon)

    def way(self, way):
        structure_type = _structure_type(way.tags)
        if structure_type is not None:
            coordinates = [
                (node.location.lat, node.location.lon)
                for node in way.nodes
                if node.location.valid()
            ]
            if len(coordinates) >= 2:
                tag_values = {tag.k: tag.v for tag in way.tags}
                name = tag_values.get("name") or tag_values.get("name:zh") or structure_type
                self.pending_structures.append((
                    way.id,
                    structure_type,
                    name,
                    json.dumps(coordinates),
                    json.dumps(tag_values, ensure_ascii=False),
                ))
        if way.tags.get("highway") != "steps":
            return
        coordinates = [
            (node.location.lat, node.location.lon)
            for node in way.nodes
            if node.location.valid()
        ]
        if not coordinates:
            return
        latitude = sum(point[0] for point in coordinates) / len(coordinates)
        longitude = sum(point[1] for point in coordinates) / len(coordinates)
        self._add("way", way.id, "steps", way.tags, latitude, longitude)

    def flush(self):
        for feature in self.pending:
            cursor = self.connection.execute(
                """
                INSERT INTO facilities (
                    osm_type, osm_id, facility_type, name, latitude, longitude, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                feature,
            )
            self.connection.execute(
                "INSERT INTO facility_spatial_index VALUES (?, ?, ?, ?, ?)",
                (cursor.lastrowid, feature[5], feature[5], feature[4], feature[4]),
            )
        self.pending.clear()
        for osm_id, structure_type, name, coords_json, tags_json in self.pending_structures:
            coordinates = json.loads(coords_json)
            latitudes = [point[0] for point in coordinates]
            longitudes = [point[1] for point in coordinates]
            cursor = self.connection.execute(
                """
                INSERT INTO structure_ways (osm_id, structure_type, name, coords_json, tags_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (osm_id, structure_type, name, coords_json, tags_json),
            )
            self.connection.execute(
                "INSERT INTO structure_way_spatial_index VALUES (?, ?, ?, ?, ?)",
                (cursor.lastrowid, min(longitudes), max(longitudes), min(latitudes), max(latitudes)),
            )
        self.pending_structures.clear()
        self.connection.commit()


def build_osm_index(pbf_path=DEFAULT_PBF_PATH, index_path=DEFAULT_INDEX_PATH):
    pbf_path = Path(pbf_path).resolve()
    index_path = Path(index_path).resolve()
    if not pbf_path.is_file():
        raise FileNotFoundError(f"OSM PBF file not found: {pbf_path}")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = index_path.with_suffix(index_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary_path)
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        _create_schema(connection)
        handler = _FacilityHandler(connection)
        handler.apply_file(str(pbf_path), locations=True)
        handler.flush()
        metadata = {
            "pbf_path": str(pbf_path),
            "pbf_size": str(pbf_path.stat().st_size),
            "pbf_mtime_ns": str(pbf_path.stat().st_mtime_ns),
            "built_at": datetime.now(timezone.utc).isoformat(),
        }
        connection.executemany("INSERT INTO metadata (key, value) VALUES (?, ?)", metadata.items())
        connection.execute("ANALYZE")
        connection.commit()
    except Exception:
        connection.close()
        temporary_path.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    temporary_path.replace(index_path)
    logger.info("Built local OSM index at %s; feature counts=%s", index_path, handler.type_counts)
    return dict(handler.type_counts)


def is_index_current(index_path=DEFAULT_INDEX_PATH, pbf_path=DEFAULT_PBF_PATH):
    index_path = Path(index_path)
    pbf_path = Path(pbf_path)
    if not index_path.is_file() or not pbf_path.is_file():
        return False
    try:
        with sqlite3.connect(index_path) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        pbf_stat = pbf_path.stat()
        return (
            metadata.get("pbf_size") == str(pbf_stat.st_size)
            and metadata.get("pbf_mtime_ns") == str(pbf_stat.st_mtime_ns)
        )
    except (sqlite3.Error, OSError):
        return False


def query_osm_index(bbox, index_path=DEFAULT_INDEX_PATH):
    south, west, north, east = bbox
    with sqlite3.connect(f"file:{Path(index_path).resolve().as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT f.osm_type, f.osm_id, f.facility_type, f.name,
                   f.latitude, f.longitude, f.tags_json
            FROM facility_spatial_index AS spatial
            JOIN facilities AS f ON f.id = spatial.id
            WHERE spatial.min_longitude <= ? AND spatial.max_longitude >= ?
              AND spatial.min_latitude <= ? AND spatial.max_latitude >= ?
            """,
            (east, west, north, south),
        ).fetchall()

    elements = []
    for osm_type, osm_id, facility_type, name, latitude, longitude, tags_json in rows:
        tags = json.loads(tags_json)
        tags.setdefault("name", name)
        element = {
            "type": osm_type,
            "id": osm_id,
            "tags": tags,
        }
        if osm_type == "way":
            element["center"] = {"lat": latitude, "lon": longitude}
        else:
            element.update({"lat": latitude, "lon": longitude})
        elements.append(element)
    return elements


def query_structure_ways(bbox, index_path=DEFAULT_INDEX_PATH):
    south, west, north, east = bbox
    index_path = Path(index_path).resolve()
    if not index_path.is_file():
        return []
    try:
        with sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                """
                SELECT w.structure_type, w.name, w.coords_json, w.tags_json
                FROM structure_way_spatial_index AS spatial
                JOIN structure_ways AS w ON w.id = spatial.id
                WHERE spatial.min_longitude <= ? AND spatial.max_longitude >= ?
                  AND spatial.min_latitude <= ? AND spatial.max_latitude >= ?
                """,
                (east, west, north, south),
            ).fetchall()
    except sqlite3.Error:
        # 舊版索引沒有 structure_ways 資料表，優雅降級
        return []

    return [
        {
            "structure_type": structure_type,
            "name": name,
            "coords": json.loads(coords_json),
            "tags": json.loads(tags_json),
        }
        for structure_type, name, coords_json, tags_json in rows
    ]


# 更新記錄：2026-10-09 新增 PBF 串流匯入、SQLite RTree 索引及 bbox 設施查詢。