import rasterio
from config import DEM_PATH
from dem_processing import compute_slopes, correct_structural_elevations, smooth_elevations

def main():
    # 打開 DEM 檔案
    ds = rasterio.open(DEM_PATH)

    print("=== DEM 檔案資訊 ===")
    print("檔案路徑:", DEM_PATH)
    print("座標系統 (CRS):", ds.crs)
    print("範圍 (bounds):", ds.bounds)
    print("解析度 (resolution):", ds.res)
    print("大小 (width x height):", ds.width, "x", ds.height)

    # 讀取一個像素的高程值
    band1 = ds.read(1)
    sample_value = band1[0, 0]  # 左上角像素
    print("左上角像素高程值:", sample_value)

def test_smoothing_reduces_isolated_elevation_jumps():
    samples = [
        (114.17 + index * 0.00005, 22.32, elevation)
        for index, elevation in enumerate([10, 10, 10, 12, 12, 10, 10, 10, 10])
    ]

    raw_slopes = compute_slopes(samples)
    smoothed_slopes = compute_slopes(smooth_elevations(samples))
    raw_max = max(abs(segment["slope_deg"]) for segment in raw_slopes)
    smoothed_max = max(abs(segment["slope_deg"]) for segment in smoothed_slopes)

    assert raw_max > 10
    assert smoothed_max < 10


def test_structural_correction_flattens_bridge_deck():
    # 模擬天橋下地面下陷：高程 40 -> 14 -> 40，橋面本身應接近水平
    lons = [114.17 + index * 0.00005 for index in range(8)]
    samples = [(lon, 22.32, elevation) for lon, elevation in zip(lons, [40, 40, 40, 14, 14, 40, 40, 40])]
    bridge = {"structure_type": "bridge", "coords": [[22.32, lons[2]], [22.32, lons[5]]]}

    corrected, indices = correct_structural_elevations(samples, [bridge], threshold_m=2.0)
    slopes = compute_slopes(corrected)

    assert indices == {2, 3, 4, 5}
    assert max(abs(segment["slope_deg"]) for segment in slopes) < 5


def test_structural_correction_without_structures_keeps_samples():
    samples = [(114.17 + index * 0.00005, 22.32, 10 + index) for index in range(5)]
    corrected, indices = correct_structural_elevations(samples, [])

    assert corrected == samples
    assert indices == set()

if __name__ == "__main__":
    main()
