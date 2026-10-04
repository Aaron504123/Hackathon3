import math, rasterio
import numpy as np
from pathlib import Path
from shapely.geometry import LineString
from pyproj import Transformer
from geographiclib.geodesic import Geodesic
from config import DEM_PATH, SAMPLE_DISTANCE_M

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
