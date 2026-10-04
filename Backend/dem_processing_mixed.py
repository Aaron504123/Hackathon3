import rasterio
import numpy as np
from shapely.geometry import LineString
from pyproj import Transformer
import math
from config import DEM_PATH

def process_dem_route_mixed(coords, sample_distance, threshold=200):
    """
    threshold: 當抽樣點數超過這個值時，改用 NumPy 向量化
    """

    # WGS84 → EPSG:2326
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)
    transformed_coords = [transformer.transform(lon, lat) for lon, lat in coords]

    # 建立路徑並抽樣
    line = LineString(transformed_coords)
    sample_points = [line.interpolate(d) for d in range(0, int(line.length), int(sample_distance))]

    # 讀取 DEM 高程
    ds = rasterio.open(DEM_PATH)
    band1 = ds.read(1)
    elevations = [float(band1[ds.index(pt.x, pt.y)[0], ds.index(pt.x, pt.y)[1]]) for pt in sample_points]

    # 判斷使用哪種方法
    if len(sample_points) > threshold:
        # 向量化版本
        distances = np.arange(0, int(line.length), int(sample_distance))
        dz = np.diff(elevations)
        dx = np.diff(distances)
        slopes = np.degrees(np.arctan2(dz, dx)).tolist()
    else:
        # 迴圈版本
        slopes = []
        for i in range(len(sample_points)-1):
            dx = sample_points[i+1].distance(sample_points[i])
            dz = elevations[i+1] - elevations[i]
            slope_deg = math.degrees(math.atan2(dz, dx))
            slopes.append(slope_deg)

    return slopes
