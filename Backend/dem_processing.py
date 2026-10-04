import math, rasterio
import numpy as np
from pathlib import Path
from shapely.geometry import LineString
from pyproj import Transformer
from geographiclib.geodesic import Geodesic
from config import DEM_PATH, SAMPLE_DISTANCE_M, SLOPE_SMOOTHING_WINDOW_M

if not Path(DEM_PATH).exists():
    raise FileNotFoundError(f"DEM file not found: {DEM_PATH}")

_dem_ds = rasterio.open(DEM_PATH)
_dem_crs = _dem_ds.crs
_to_dem = Transformer.from_crs("EPSG:4326", _dem_crs, always_xy=True)
_to_wgs = Transformer.from_crs(_dem_crs, "EPSG:4326", always_xy=True)

def sample_elevations(coords, sample_distance_m=SAMPLE_DISTANCE_M):
    line = LineString(coords)
    dem_coords = [_to_dem.transform(lon, lat) for lon, lat in line.coords]
    dem_line = LineString(dem_coords)
    length = dem_line.length
    n = max(2, int(math.ceil(length/sample_distance_m))+1)
    locations = []
    dem_points = []
    for i in range(n):
        pt = dem_line.interpolate(i/(n-1)*length)
        x, y = pt.x, pt.y
        lon, lat = _to_wgs.transform(x, y)
        locations.append((lon, lat))
        dem_points.append((x, y))

    samples = []
    for (lon, lat), values in zip(
        locations,
        _dem_ds.sample(dem_points, indexes=1, masked=True),
    ):
        value = values[0]
        elev = None if np.ma.is_masked(value) else float(value)
        if elev == _dem_ds.nodata:
            elev = None
        samples.append((lon, lat, elev))

    return samples

def smooth_elevations(samples, window_m=SLOPE_SMOOTHING_WINDOW_M):
    if len(samples) < 3 or window_m <= 0:
        return samples

    geod = Geodesic.WGS84
    distances = [0.0]
    for index in range(len(samples) - 1):
        lon1, lat1, _ = samples[index]
        lon2, lat2, _ = samples[index + 1]
        distances.append(distances[-1] + geod.Inverse(lat1, lon1, lat2, lon2)['s12'])

    smoothed = []
    left = 0
    right = 0
    half_window = window_m / 2

    for index, (lon, lat, elevation) in enumerate(samples):
        distance = distances[index]
        while distances[left] < distance - half_window:
            left += 1
        while right < len(samples) and distances[right] <= distance + half_window:
            right += 1

        window = [
            (distances[sample_index] - distance, samples[sample_index][2])
            for sample_index in range(left, right)
            if samples[sample_index][2] is not None
        ]

        if elevation is None or len(window) < 2:
            smoothed.append((lon, lat, elevation))
            continue

        mean_distance = sum(item[0] for item in window) / len(window)
        mean_elevation = sum(item[1] for item in window) / len(window)
        variance = sum((item[0] - mean_distance) ** 2 for item in window)
        if variance == 0:
            smoothed.append((lon, lat, elevation))
            continue

        covariance = sum(
            (offset - mean_distance) * (value - mean_elevation)
            for offset, value in window
        )
        trend = covariance / variance
        fitted_elevation = mean_elevation - trend * mean_distance
        smoothed.append((lon, lat, fitted_elevation))

    return smoothed

def compute_slopes(samples):
    geod = Geodesic.WGS84
    segs = []
    for i in range(len(samples)-1):
        lon1, lat1, z1 = samples[i]
        lon2, lat2, z2 = samples[i+1]
        g = geod.Inverse(lat1, lon1, lat2, lon2)
        dist = g['s12']
        if z1 is None or z2 is None or dist == 0:
            slope_deg, dz = None, None
        else:
            dz = z2 - z1
            slope_deg = math.degrees(math.atan2(dz, dist))
        segs.append({"from":[lon1,lat1],"to":[lon2,lat2],
                     "distance_m":dist,"dz_m":dz,"slope_deg":slope_deg})
    return segs
