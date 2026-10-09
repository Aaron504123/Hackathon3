import argparse
import logging
from pathlib import Path

from osm_index import DEFAULT_INDEX_PATH, DEFAULT_PBF_PATH, build_osm_index


def main():
    parser = argparse.ArgumentParser(description="Build the local Hong Kong OSM facility index.")
    parser.add_argument("--pbf", type=Path, default=DEFAULT_PBF_PATH, help="Source OSM PBF file")
    parser.add_argument("--output", type=Path, default=DEFAULT_INDEX_PATH, help="SQLite index output path")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    counts = build_osm_index(arguments.pbf, arguments.output)
    print(f"Index created: {arguments.output.resolve()}")
    print(f"Features: {counts}")


if __name__ == "__main__":
    main()


# 更新記錄：2026-10-09 新增命令列工具以從香港 PBF 建立本地設施索引。