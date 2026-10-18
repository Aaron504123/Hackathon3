"""把地政總署三維行人道路網（3DPN FGDB）轉換成本地 SQLite + RTree 索引。

來源：Backend/data/3DPN_P2.gdb 的 PedestrianRoute 圖層（EPSG:2326 + HKPD 高程）。
輸出：Backend/data/pedestrian-network.sqlite3（WGS84 2D WKB 幾何 + 屬性 + 空間索引）。
"""
import argparse
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pyogrio.raw import read
from pyproj import Transformer
from shapely import from_wkb, to_wkb
from shapely.ops import transform as shapely_transform

logger = logging.getLogger(__name__)
BACKEND_DIR = Path(__file__).resolve().parent
DEFAULT_GDB_PATH = Path(os.getenv("PEDESTRIAN_GDB_PATH", BACKEND_DIR / "data" / "3DPN_P2.gdb"))
DEFAULT_INDEX_PATH = Path(os.getenv("PEDESTRIAN_INDEX_PATH", BACKEND_DIR / "data" / "pedestrian-network.sqlite3"))

COLUMNS = [
    "PedestrianRouteID", "Location", "FeatureType", "CrossingFeature",
    "WeatherProof", "WheelchairBarrier", "WheelchairAccess", "Gradient",
    "StreetNameEN", "StreetNameTC",
]

_to_wgs = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)


def _create_schema(connection):
    connection.executescript(
        """
        CREATE TABLE segments (
            id INTEGER PRIMARY KEY,
            route_id INTEGER NOT NULL,
            feature_type INTEGER NOT NULL,
            location INTEGER,
            crossing_feature INTEGER,
            weather_proof INTEGER,
            wheelchair_barrier INTEGER,
            wheelchair_access INTEGER,
            gradient REAL,
            street_name_tc TEXT,
            street_name_en TEXT,
            geom_wkb BLOB NOT NULL
        );
        CREATE VIRTUAL TABLE segments_rtree USING rtree(
            id, min_longitude, max_longitude, min_latitude, max_latitude
        );
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE INDEX segments_type_index ON segments(feature_type);
        """
    )


def _optional_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_pedestrian_index(gdb_path=DEFAULT_GDB_PATH, index_path=DEFAULT_INDEX_PATH, chunk_size=50000):
    gdb_path = Path(gdb_path).resolve()
    index_path = Path(index_path).resolve()
    if not gdb_path.is_dir():
        raise FileNotFoundError(f"3DPN FGDB not found: {gdb_path}")

    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = index_path.with_suffix(index_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary_path)
    total = 0
    try:
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        _create_schema(connection)

        skip = 0
        while True:
            meta, _, geometries, fields = read(
                str(gdb_path), layer="PedestrianRoute",
                columns=COLUMNS, skip_features=skip, max_features=chunk_size,
            )
            if geometries is None or len(geometries) == 0:
                break
            names = list(meta["fields"])

            def column(name):
                return fields[names.index(name)]

            for row_index, wkb in enumerate(geometries):
                geometry = from_wkb(bytes(wkb))
                if geometry.is_empty:
                    continue
                geometry = shapely_transform(_to_wgs.transform, geometry)
                min_x, min_y, max_x, max_y = geometry.bounds
                cursor = connection.execute(
                    """
                    INSERT INTO segments (
                        route_id, feature_type, location, crossing_feature, weather_proof,
                        wheelchair_barrier, wheelchair_access, gradient,
                        street_name_tc, street_name_en, geom_wkb
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(column("PedestrianRouteID")[row_index]),
                        int(column("FeatureType")[row_index]),
                        _optional_int(column("Location")[row_index]),
                        _optional_int(column("CrossingFeature")[row_index]),
                        _optional_int(column("WeatherProof")[row_index]),
                        _optional_int(column("WheelchairBarrier")[row_index]),
                        _optional_int(column("WheelchairAccess")[row_index]),
                        _optional_float(column("Gradient")[row_index]),
                        str(column("StreetNameTC")[row_index] or ""),
                        str(column("StreetNameEN")[row_index] or ""),
                        to_wkb(geometry, output_dimension=2),
                    ),
                )
                connection.execute(
                    "INSERT INTO segments_rtree VALUES (?, ?, ?, ?, ?)",
                    (cursor.lastrowid, min_x, max_x, min_y, max_y),
                )
                total += 1

            connection.commit()
            skip += len(geometries)
            logger.info("converted %d segments so far", total)

        metadata = {
            "gdb_path": str(gdb_path),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "segment_count": str(total),
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
    logger.info("Built pedestrian network index at %s; segments=%d", index_path, total)
    return total


def main():
    parser = argparse.ArgumentParser(description="Build the local 3D Pedestrian Network index.")
    parser.add_argument("--gdb", type=Path, default=DEFAULT_GDB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_INDEX_PATH)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    count = build_pedestrian_index(arguments.gdb, arguments.output)
    print(f"Index created: {arguments.output.resolve()}")
    print(f"Segments: {count}")


if __name__ == "__main__":
    main()


# 更新記錄：2026-10-17 新增 3DPN FGDB 轉 SQLite/RTree 本地索引工具（分批讀取、2326→WGS84 轉換）。
