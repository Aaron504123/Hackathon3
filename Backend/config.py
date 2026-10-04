from pathlib import Path

# config.py
# 請修改以下值為你的實際設定（不要把 Key 放到前端）

# Mapbox
MAPBOX_TOKEN = "pk.eyJ1IjoiYWFyb241MDQxMjMiLCJhIjoiY211cjZwNmx6MDA0eTJ4b2Q3M3R4MHdsayJ9.lGndsS1VXmYGTOFrg2LPGg"

# Barrier Free Map API (NGO)
BARRIER_FREE_API_BASE = "https://wpc-pwa-api.barrierfreemap.hk/api"
BARRIER_FREE_API_KEY = "YOUR_BARRIER_FREE_API_KEY"

# Overpass API
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# DEM (GeoTIFF) 路徑（伺服器上）
BASE_DIR = Path(__file__).resolve().parent
DEM_PATH = str((BASE_DIR / "data" / "Digital Terrain Model.tif").resolve())

# 抽樣與閾值
SAMPLE_DISTANCE_M = 2.0   # 沿路抽樣間距 (公尺)
MAX_ACCEPTABLE_SLOPE_DEG = 6.0  # 前端顯示閾值（示意）


# 快取 (選用 Redis)
USE_REDIS = False
REDIS_URL = "redis://redis:6379/0"

# 其他
REQUEST_TIMEOUT = 15  # 外部 API 請求超時 (秒)
