import math, rasterio
import numpy as np
from pathlib import Path
from shapely.geometry import LineString, Point
from pyproj import Transformer
from geographiclib.geodesic import Geodesic
from config import DEM_PATH, SAMPLE_DISTANCE_M, SLOPE_SMOOTHING_WINDOW_M

if not Path(DEM_PATH).exists():
    raise FileNotFoundError(f"DEM file not found: {DEM_PATH}")

_dem_ds = rasterio.open(DEM_PATH)
_dem_crs = _dem_ds.crs
_to_dem = Transformer.from_crs("EPSG:4326", _dem_crs, always_xy=True)
_to_wgs = Transformer.from_crs(_dem_crs, "EPSG:4326", always_xy=True)
_to_metric = Transformer.from_crs("EPSG:4326", "EPSG:2326", always_xy=True)

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

def correct_structural_elevations(samples, structures, threshold_m=12.0):
    """修正天橋／隧道段的高程假坡度。

    DEM 是裸地地形模型，沒有橋面或隧道結構；路線經過天橋時，取樣到的是
    橋下地面（或路塹）高程，會在橋段產生虛假陡坡。此函數把落在結構線上
    的連續取樣點高程，改為以結構兩端地面高程線性內插，近似水平的橋面。

    回傳 (修正後 samples, 被修正的索引集合)。
    """
    if not structures:
        return samples, set()

    lines = []
    for structure in structures:
        coords = structure.get("coords") or []
        if len(coords) < 2:
            continue
        metric_coords = [_to_metric.transform(lon, lat) for lat, lon in coords]
        lines.append(LineString(metric_coords))
    if not lines:
        return samples, set()

    on_structure = []
    for lon, lat, _ in samples:
        x, y = _to_metric.transform(lon, lat)
        point = Point(x, y)
        on_structure.append(any(line.distance(point) <= threshold_m for line in lines))

    corrected = list(samples)
    corrected_indices = set()
    n = len(samples)
    index = 0
    while index < n:
        if not on_structure[index]:
            index += 1
            continue
        run_end = index
        while run_end + 1 < n and on_structure[run_end + 1]:
            run_end += 1

        left_elev = next(
            (samples[j][2] for j in range(index - 1, -1, -1) if samples[j][2] is not None),
            None,
        )
        right_elev = next(
            (samples[j][2] for j in range(run_end + 1, n) if samples[j][2] is not None),
            None,
        )

        for j in range(index, run_end + 1):
            lon, lat, _ = samples[j]
            if left_elev is None and right_elev is None:
                continue
            if left_elev is None:
                new_elev = right_elev
            elif right_elev is None:
                new_elev = left_elev
            else:
                ratio = (j - index + 1) / (run_end - index + 2)
                new_elev = left_elev + (right_elev - left_elev) * ratio
            corrected[j] = (lon, lat, new_elev)
            corrected_indices.add(j)

        index = run_end + 1

    return corrected, corrected_indices


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
