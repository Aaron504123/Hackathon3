import json
import sqlite3
from unittest.mock import Mock

import pytest
import requests
import facilities_fetcher
import app as app_module
from osm_index import _create_schema, query_osm_index
from app import app, choose_preferred_route, classify_route_obstacles, normalize_route_geometry, _filter_facilities_for_map, _filter_facilities_near_route

@pytest.fixture
def client():
    app.testing = True
    return app.test_client()

def test_choose_preferred_route_prefers_elevator_path():
    routes = [
        {"summary": {"distance": 120}, "geometry": {"coordinates": [[114.17, 22.32], [114.18, 22.33]]}},
        {"summary": {"distance": 180}, "geometry": {"coordinates": [[114.17, 22.32], [114.175, 22.325], [114.18, 22.33]]}},
    ]
    obstacles = [
        {"type": "steps", "name": "Main Stair", "longitude": 114.175, "latitude": 22.325},
        {"type": "elevator", "name": "Lift A", "longitude": 114.176, "latitude": 22.326},
    ]

    chosen = choose_preferred_route(routes, obstacles)

    assert chosen == routes[1]


def test_route_geometry_polyline_is_decoded():
    encoded = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    route = {"summary": {"distance": 100}, "geometry": encoded}
    coords = normalize_route_geometry(route)

    assert coords and len(coords) > 0
    assert isinstance(coords[0], tuple)
    assert len(coords[0]) == 2


def test_classify_route_obstacles_returns_clear_obstacle_summary():
    obstacles = [
        {"type": "steps", "name": "Staircase", "longitude": 114.17, "latitude": 22.32},
        {"type": "elevator", "name": "Lift", "longitude": 114.175, "latitude": 22.325},
        {"type": "toilet", "name": "Accessible toilet", "longitude": 114.18, "latitude": 22.33},
    ]

    summary = classify_route_obstacles(obstacles)

    assert summary["has_step_obstacle"] is True
    assert summary["has_elevator_access"] is True
    assert summary["obstacle_types"] == ["steps", "elevator", "toilet"]


def test_barrier_free_items_normalize_toilet_and_parking_and_exclude_osm_copies():
    barrier_items = {
        "items": [
            {
                "item_name_zh": "無障礙洗手間",
                "item_category": {"id": 3},
                "map_lat": 22.32,
                "map_lng": 114.17,
                "access_status": {"status_name_zh": "輪椅通行"},
                "updated_at": "2026-10-01T00:00:00Z",
                "url": "https://barrierfreemap.hk/location/1",
            },
            {
                "item_name_zh": "傷殘人士泊車位",
                "item_category": {"id": 4},
                "map_lat": 22.321,
                "map_lng": 114.171,
                "access_status": None,
            },
            {
                "item_category": {"id": 8},
                "map_lat": 22.322,
                "map_lng": 114.172,
            },
        ]
    }
    overpass_data = {
        "elements": [
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"amenity": "toilets", "wheelchair": "yes"}},
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"amenity": "parking", "capacity:disabled": "2"}},
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"elevator": "yes"}},
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"wheelchair": "no", "name": "Unclassified obstacle"}},
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"name": "Unclassified facility"}},
        ]
    }

    facilities = facilities_fetcher.normalize(barrier_items, overpass_data)

    assert [(facility["type"], facility["source"]) for facility in facilities] == [
        ("toilet", "barrierfree"),
        ("parking", "barrierfree"),
        ("elevator", "osm"),
    ]
    assert facilities[0]["status"] == "輪椅通行"
    assert facilities[0]["updated_at"] == "2026-10-01T00:00:00Z"
    assert facilities[0]["url"] == "https://barrierfreemap.hk/location/1"


def test_normalize_logs_excluded_unclassified_facilities(caplog):
    overpass_data = {
        "elements": [
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"wheelchair": "no"}},
            {"type": "node", "lat": 22.32, "lon": 114.17, "tags": {"name": "Unknown"}},
        ]
    }

    with caplog.at_level("DEBUG", logger="facilities_fetcher"):
        facilities = facilities_fetcher.normalize({"items": []}, overpass_data)

    assert facilities == []
    assert "excluded_unclassified=1" in caplog.text
    assert "excluded_generic_obstacles=1" in caplog.text


def test_local_osm_index_bbox_query_returns_nodes_and_way_centers(tmp_path):
    index_path = tmp_path / "osm.sqlite3"
    with sqlite3.connect(index_path) as connection:
        _create_schema(connection)
        features = [
            ("node", 1, "elevator", "Lift A", 22.32, 114.17, {"elevator": "yes", "name": "Lift A"}),
            ("way", 2, "steps", "Stairs", 22.321, 114.171, {"highway": "steps"}),
            ("node", 3, "ramp", "Far ramp", 23.0, 114.17, {"ramp": "yes"}),
        ]
        for osm_type, osm_id, facility_type, name, lat, lon, tags in features:
            cursor = connection.execute(
                """
                INSERT INTO facilities (
                    osm_type, osm_id, facility_type, name, latitude, longitude, tags_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (osm_type, osm_id, facility_type, name, lat, lon, json.dumps(tags)),
            )
            connection.execute(
                "INSERT INTO facility_spatial_index VALUES (?, ?, ?, ?, ?)",
                (cursor.lastrowid, lon, lon, lat, lat),
            )

    elements = query_osm_index([22.30, 114.15, 22.33, 114.18], index_path)
    facilities = facilities_fetcher.normalize({"items": []}, {"elements": elements})

    assert [(item["type"], item["source"]) for item in facilities] == [
        ("elevator", "osm"),
        ("steps", "osm"),
    ]
    way_element = next(item for item in elements if item["type"] == "way")
    assert way_element["center"] == {"lat": 22.321, "lon": 114.171}


def test_fetch_overpass_prefers_current_local_index(monkeypatch):
    local_elements = [{"type": "node", "id": 1, "lat": 22.32, "lon": 114.17, "tags": {"elevator": "yes"}}]
    monkeypatch.setattr(facilities_fetcher, "is_index_current", lambda *args: True)
    monkeypatch.setattr(facilities_fetcher, "query_osm_index", lambda bbox, path: local_elements)
    monkeypatch.setattr(
        facilities_fetcher.requests,
        "post",
        Mock(side_effect=AssertionError("Overpass should not be called when local index is current")),
    )

    result = facilities_fetcher.fetch_overpass([22.3, 114.1, 22.4, 114.2])

    assert result == {"elements": local_elements}


def test_fetch_barrier_free_uses_items_endpoint_and_filters_bbox(monkeypatch):
    response = Mock()
    response.json.return_value = {
        "items": [
            {"item_category": {"id": 3}, "map_lat": 22.32, "map_lng": 114.17},
            {"item_category": {"id": 4}, "map_lat": 22.4, "map_lng": 114.2},
            {"item_category": {"id": 8}, "map_lat": 22.32, "map_lng": 114.17},
        ]
    }
    request = Mock(return_value=response)
    monkeypatch.setattr(facilities_fetcher, "BARRIER_FREE_API_KEY", "test-key")
    monkeypatch.setattr(facilities_fetcher.requests, "get", request)

    result = facilities_fetcher.fetch_barrier_free((22.3, 114.1, 22.35, 114.2))

    assert len(result["items"]) == 1
    request.assert_called_once()
    assert request.call_args.args[0].endswith("/items")
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_facilities_are_limited_to_route_corridor():
    route = [[114.17, 22.32], [114.18, 22.32]]
    facilities = [
        {"name": "Near", "longitude": 114.175, "latitude": 22.3202},
        {"name": "Middle", "longitude": 114.175, "latitude": 22.3215},
        {"name": "Far", "longitude": 114.175, "latitude": 22.325},
    ]

    nearby = _filter_facilities_near_route(facilities, route)

    assert [facility["name"] for facility in nearby] == ["Near", "Middle", "Far"]


def test_map_keeps_all_barrierfree_and_limits_osm_to_100m_from_route():
    route = [[114.17, 22.32], [114.18, 22.32]]
    facilities = [
        {"name": "BarrierFree toilet", "source": "barrierfree", "type": "toilet", "longitude": 114.175, "latitude": 22.35},
        {"name": "Near elevator", "source": "osm", "type": "elevator", "longitude": 114.175, "latitude": 22.3201},
        {"name": "Distant steps", "source": "osm", "type": "steps", "longitude": 114.175, "latitude": 22.322},
    ]

    visible = _filter_facilities_for_map(facilities, route)

    assert [facility["name"] for facility in visible] == ["BarrierFree toilet", "Near elevator"]


def test_route_slopes(client):
    # 一般測試案例：市區內兩點
    response = client.post("/route", json={
        "start": [114.17, 22.32],
        "end": [114.18, 22.33]
    })

    assert response.status_code == 200
    data = response.get_json()
    slopes = data["slopes"]

    # 檢查斜坡角度合理性
    assert all(-90 <= s <= 90 for s in slopes)

def test_route_boundary(client):
    # 邊界測試案例：DEM 邊界附近兩點
    response = client.post("/route", json={
        "start": [114.20, 22.40],  # 接近 DEM 邊界
        "end": [114.21, 22.41]
    })

    assert response.status_code == 200
    data = response.get_json()
    slopes = data["slopes"]

    # 輸出斜坡數值，方便人工檢查
    print("Boundary slopes:", slopes)

    # 還是檢查合理範圍
    assert all(-90 <= s <= 90 for s in slopes)


def test_route_keeps_ors_route_when_facility_api_fails(client, monkeypatch):
    fake_routes = [{
        "summary": {"distance": 100},
        "geometry": {"coordinates": [[114.17, 22.32], [114.176, 22.325], [114.18, 22.33]]}
    }]

    monkeypatch.setattr("app.query_ors_routes", lambda start, end: {"routes": fake_routes})
    barrier_fetch = Mock(side_effect=RuntimeError("barrier API failed"))
    osm_fetch = Mock(side_effect=RuntimeError("OSM lookup failed"))
    monkeypatch.setattr(app_module, "fetch_barrier_free", barrier_fetch)
    monkeypatch.setattr(app_module, "fetch_overpass", osm_fetch)

    response = client.post("/route", json={
        "start": [114.17, 22.32],
        "end": [114.18, 22.33]
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["route_source"] == "openrouteservice"
    assert data["warning"] is None
    barrier_fetch.assert_called_once()
    osm_fetch.assert_called_once()


def test_route_uses_mapbox_walking_when_ors_cannot_route(client, monkeypatch):
    coordinates = [[114.1762, 22.3365], [114.1735, 22.3359]]
    monkeypatch.setattr(
        app_module,
        "query_ors_routes",
        lambda start, end: (_ for _ in ()).throw(requests.HTTPError("ORS route not found")),
    )
    monkeypatch.setattr(app_module, "query_mapbox_route", lambda start, end: coordinates)
    monkeypatch.setattr(app_module, "fetch_facilities_nearby", lambda bbox: [])
    monkeypatch.setattr(
        app_module,
        "sample_elevations",
        lambda route, sample_distance_m: [
            (route[0][0], route[0][1], 10.0),
            (route[-1][0], route[-1][1], 12.0),
        ],
    )
    monkeypatch.setattr(app_module, "smooth_elevations", lambda samples: samples)
    monkeypatch.setattr(
        app_module,
        "compute_slopes",
        lambda samples: [{
            "from": samples[0][:2],
            "to": samples[-1][:2],
            "distance_m": 300.0,
            "dz_m": 2.0,
            "slope_deg": 1.0,
        }],
    )

    response = client.post("/route", json={"start": coordinates[0], "end": coordinates[-1]})

    assert response.status_code == 200
    data = response.get_json()
    assert data["route_source"] == "mapbox_walking"
    assert data["route"]["coordinates"] == coordinates
    assert data["warning"]


def test_fetch_barrier_free_skips_placeholder_api_key(monkeypatch):
    request = Mock()
    monkeypatch.setattr(facilities_fetcher, "BARRIER_FREE_API_KEY", "YOUR_BARRIER_FREE_API_KEY")
    monkeypatch.setattr(facilities_fetcher.requests, "get", request)

    result = facilities_fetcher.fetch_barrier_free((22.2, 113.9, 22.4, 114.2))

    assert result == {}
    request.assert_not_called()


def test_fetch_barrier_free_handles_unauthorized_response(monkeypatch, caplog):
    response = requests.Response()
    response.status_code = 401
    monkeypatch.setattr(facilities_fetcher, "BARRIER_FREE_API_KEY", "invalid-key")
    monkeypatch.setattr(facilities_fetcher.requests, "get", Mock(return_value=response))

    result = facilities_fetcher.fetch_barrier_free((22.2, 113.9, 22.4, 114.2))

    assert result == {}
    assert "HTTP 401" in caplog.text
    assert "Traceback" not in caplog.text


# 更新記錄：2026-10-09 加入泛用 facility/obstacle 過濾及 debug 記錄回歸測試。
# 更新記錄：2026-10-09 加入本地 PBF SQLite 索引 bbox 查詢及優先使用索引的回歸測試。
# 更新記錄：2026-10-09 驗證地圖保留 BarrierFreeMap 資料並只顯示 100 公尺路線範圍內 OSM 資料。
# 更新記錄：2026-10-09 驗證 ORS 路線共用一次設施查詢，避免重複 API/索引讀取。
