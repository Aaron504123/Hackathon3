"""3DPN 行人網絡索引的回歸測試。"""
import sqlite3

from shapely import LineString, to_wkb

from pedestrian_index import (
    analyze_route_wheelchair,
    fetch_pedestrian_facilities,
    merge_pedestrian_priority,
    query_pedestrian_segments,
    query_pedestrian_structures,
    validate_route_pedestrian_network,
)


def _build_test_index(path):
    """建立小型測試索引：一條樓梯、一部升降機、一條行人天橋、一條普通行人路。"""
    from build_pedestrian_index import _create_schema

    connection = sqlite3.connect(path)
    _create_schema(connection)
    samples = [
        # (route_id, feature_type, wheelchair_access, wheelchair_barrier, gradient, street_tc, line)
        (1, 12, 2, 1, 0.5, "測試街", LineString([(114.1700, 22.3200), (114.1701, 22.3201)])),  # 樓梯
        (2, 10, 1, 2, 0.0, "測試街", LineString([(114.1702, 22.3202), (114.1703, 22.3203)])),  # 升降機
        (3, 2, 1, 2, 0.0, "天橋", LineString([(114.1704, 22.3204), (114.1708, 22.3204)])),      # 行人天橋
        (4, 1, 1, 2, 0.02, "測試街", LineString([(114.1700, 22.3200), (114.1710, 22.3200)])),   # 行人路
        (5, 4, 1, 2, 0.0, "隧道", LineString([(114.1712, 22.3206), (114.1715, 22.3206)])),
        (6, 7, 2, 1, 0.0, "車輛出入口", LineString([(114.1720, 22.3220), (114.1730, 22.3220)])),
    ]
    for route_id, feature_type, wa, wb, gradient, street, geom in samples:
        min_x, min_y, max_x, max_y = geom.bounds
        cursor = connection.execute(
            """
            INSERT INTO segments (
                route_id, feature_type, location, crossing_feature, weather_proof,
                wheelchair_barrier, wheelchair_access, gradient,
                street_name_tc, street_name_en, geom_wkb
            ) VALUES (?, ?, 1, NULL, 2, ?, ?, ?, ?, '', ?)
            """,
            (route_id, feature_type, wb, wa, gradient, street, to_wkb(geom)),
        )
        connection.execute(
            "INSERT INTO segments_rtree VALUES (?, ?, ?, ?, ?)",
            (cursor.lastrowid, min_x, max_x, min_y, max_y),
        )
    connection.commit()
    connection.close()


def test_query_pedestrian_segments_and_facilities(tmp_path):
    index = tmp_path / "ped.sqlite3"
    _build_test_index(index)
    bbox = (22.3199, 114.1699, 22.3210, 114.1716)

    segments = query_pedestrian_segments(bbox, index)
    assert len(segments) == 5

    facilities = fetch_pedestrian_facilities(bbox, index)
    types = sorted(f["type"] for f in facilities)
    assert types == ["elevator", "steps"]
    assert all(f["source"] == "3dpn" for f in facilities)

    structures = query_pedestrian_structures(bbox, index)
    assert {structure["structure_type"] for structure in structures} == {"bridge", "tunnel"}
    assert all(len(structure["coords"]) >= 2 for structure in structures)


def test_pedestrian_route_validation_rejects_vehicle_entrance(tmp_path):
    index = tmp_path / "ped.sqlite3"
    _build_test_index(index)

    pedestrian_route = validate_route_pedestrian_network(
        [[114.1700, 22.3200], [114.1710, 22.3200]], index_path=index,
    )
    vehicle_entrance_route = validate_route_pedestrian_network(
        [[114.1720, 22.3220], [114.1730, 22.3220]], index_path=index,
    )

    assert pedestrian_route["verified"] is True
    assert pedestrian_route["match_ratio"] == 1.0
    assert vehicle_entrance_route["verified"] is False
    assert vehicle_entrance_route["match_ratio"] == 0.0


def test_merge_pedestrian_priority_dedupes_osm():
    pdpn = [
        {"type": "steps", "latitude": 22.3200, "longitude": 114.1700, "name": "測試街樓梯", "source": "3dpn"},
    ]
    osm = [
        {"type": "steps", "latitude": 22.32001, "longitude": 114.17001, "name": "osm steps"},  # 距離 <2m，應剔除
        {"type": "steps", "latitude": 22.3300, "longitude": 114.1800, "name": "osm far steps"},  # 距離遠，應保留
        {"type": "toilet", "latitude": 22.3200, "longitude": 114.1700, "name": "osm toilet"},   # 非障礙類，應保留
    ]

    merged = merge_pedestrian_priority(osm, pdpn)

    names = [f["name"] for f in merged]
    assert "測試街樓梯" in names
    assert "osm steps" not in names
    assert "osm far steps" in names
    assert "osm toilet" in names


def test_analyze_route_wheelchair(tmp_path):
    index = tmp_path / "ped.sqlite3"
    _build_test_index(index)
    bbox = (22.3199, 114.1699, 22.3205, 114.1711)
    coords = [[114.1700, 22.3200], [114.1705, 22.3200], [114.1710, 22.3200]]

    result = analyze_route_wheelchair(coords, bbox, index_path=index)

    assert result is not None
    assert result["matched_ratio"] >= 0.9
    assert result["barrier_free_ratio"] is not None
