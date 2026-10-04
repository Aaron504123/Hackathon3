import pytest
from app import app

@pytest.fixture
def client():
    app.testing = True
    return app.test_client()

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
