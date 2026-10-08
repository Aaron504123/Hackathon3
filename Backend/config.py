import os
from pathlib import Path

# config.py
# 請修改以下值為你的實際設定（不要把 Key 放到前端）

# Mapbox
MAPBOX_TOKEN = os.getenv("MAPBOX_TOKEN", "pk.eyJ1IjoiYWFyb241MDQxMjMiLCJhIjoiY211cjZwNmx6MDA0eTJ4b2Q3M3R4MHdsayJ9.lGndsS1VXmYGTOFrg2LPGg")

# OpenRouteService
ORS_API_KEY = os.getenv("ORS_API_KEY", "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImNmMDY0MGQ3OTM3NzQ3NGY5YTdlOTAzYjhhYWI0MTc2IiwiaCI6Im11cm11cjY0In0=")
ORS_API_BASE = "https://api.openrouteservice.org"
ORS_PROFILE = os.getenv("ORS_PROFILE", "wheelchair")

# Barrier Free Map API (NGO)
BARRIER_FREE_API_BASE = "https://wpc-pwa-api.barrierfreemap.hk/api"
BARRIER_FREE_API_KEY = os.getenv("BARRIER_FREE_API_KEY", "f313f9dc-01bf-479d-bc57-c416800422b5")

# Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# DEM (GeoTIFF) 路徑（伺服器上）
BASE_DIR = Path(__file__).resolve().parent
DEM_PATH = str((BASE_DIR / "data" / "Digital Terrain Model.tif").resolve())

# 抽樣與閾值
SAMPLE_DISTANCE_M = 5.0   # 與 DEM 解析度一致的沿路抽樣間距 (公尺)
SLOPE_SMOOTHING_WINDOW_M = 20.0  # 以局部趨勢降低單一 DEM 像素的高程跳動 (公尺)
MAX_ACCEPTABLE_SLOPE_DEG = 6.0  # 前端顯示閾值（示意）


# 快取 (選用 Redis)
USE_REDIS = False
REDIS_URL = "redis://redis:6379/0"

# 其他
REQUEST_TIMEOUT = 15  # 外部 API 請求超時 (秒)
