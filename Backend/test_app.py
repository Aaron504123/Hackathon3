import pytest
from app import app, choose_preferred_route, classify_route_obstacles, normalize_route_geometry

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
    monkeypatch.setattr("app.fetch_barrier_free", lambda bbox: (_ for _ in ()).throw(RuntimeError("barrier API failed")))
    monkeypatch.setattr("app.fetch_overpass", lambda bbox: (_ for _ in ()).throw(RuntimeError("overpass API failed")))

    response = client.post("/route", json={
        "start": [114.17, 22.32],
        "end": [114.18, 22.33]
    })

    assert response.status_code == 200
    data = response.get_json()
    assert data["route_source"] == "openrouteservice"
    assert data["warning"] is None
