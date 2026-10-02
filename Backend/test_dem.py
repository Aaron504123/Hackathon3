import rasterio
from config import DEM_PATH

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

if __name__ == "__main__":
    main()
