"""Generate a District hex-map PNG and a Markdown diagnostics report."""
from __future__ import annotations

import argparse
import colorsys
import math
import sys
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PACKAGE_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = PACKAGE_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from world_generation.generators.city_generator import (  # noqa: E402
    HEX_CELL_AREA,
    HEX_DIRECTIONS,
    HEX_NEIGHBOR_DISTANCE,
    HEX_SIDE_LENGTH,
    axial_to_xy,
    generate_city_map_with_streets,
)


DEFAULT_SEED = 42
DEFAULT_TARGET_POPULATION = 500_000
DEFAULT_CITY_TEMPLATE = "罪恶都市"
DEFAULT_OUTPUT_DIR = PACKAGE_DIR / "outputs"
IMAGE_SIZE = (2400, 1800)
STREET_IMAGE_SIZE = (3200, 2400)
IMAGE_MARGIN = 120


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _district_colors(count: int) -> list[tuple[int, int, int]]:
    """Return deterministic, hue-separated categorical colors."""
    return [
        tuple(round(channel * 255) for channel in colorsys.hsv_to_rgb(index / max(count, 1), .62, .88))
        for index in range(count)
    ]


def _hex_vertices(center: tuple[float, float]) -> list[tuple[float, float]]:
    x, y = center
    return [
        (
            x + HEX_SIDE_LENGTH * math.cos(math.radians(30 + 60 * index)),
            y + HEX_SIDE_LENGTH * math.sin(math.radians(30 + 60 * index)),
        )
        for index in range(6)
    ]


def _map_bounds(districts) -> tuple[float, float, float, float]:
    vertices = [
        vertex
        for district in districts
        for cell in district.cells
        for vertex in _hex_vertices(axial_to_xy(cell))
    ]
    return (
        min(x for x, _ in vertices),
        max(x for x, _ in vertices),
        min(y for _, y in vertices),
        max(y for _, y in vertices),
    )


def _xy_centroid(district) -> tuple[float, float]:
    centers = [axial_to_xy(cell) for cell in district.cells]
    return (
        sum(x for x, _ in centers) / len(centers),
        sum(y for _, y in centers) / len(centers),
    )


def _map_checks(districts) -> dict:
    owners: dict[tuple[int, int], str] = {}
    overlaps: list[tuple[int, int]] = []
    for district in districts:
        for cell in district.cells:
            if cell in owners:
                overlaps.append(cell)
            owners[cell] = district.name

    by_name = {district.name: district for district in districts}
    symmetric = all(
        district.name in by_name[neighbor].neighbors
        for district in districts
        for neighbor in district.neighbors
    )
    seen = set()
    pending = [districts[0].name] if districts else []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(neighbor for neighbor in by_name[name].neighbors if neighbor not in seen)
    return {
        "overlap_count": len(overlaps),
        "connected": len(seen) == len(districts),
        "neighbors_symmetric": symmetric,
    }


def build_statistics(districts, streets, seed: int, target_population: int, city_template: str) -> dict:
    checks = _map_checks(districts)
    streets_by_district = defaultdict(list)
    for street in streets:
        streets_by_district[street.district].append(street)
    rows = []
    for district in districts:
        district_streets = streets_by_district[district.name]
        street_areas = [street.area for street in district_streets]
        street_prosperities = [street.prosperity for street in district_streets]
        weighted_prosperity = sum(
            street.prosperity * street.area for street in district_streets
        ) / sum(street_areas)
        target_cells = max(1, round(district.area / HEX_CELL_AREA))
        actual_cells = len(district.cells)
        actual_area = actual_cells * HEX_CELL_AREA
        rows.append({
            "name": district.name,
            "template": district.template,
            "level": district.level,
            "population": district.population,
            "prosperity": district.prosperity,
            "configured_area": district.area,
            "target_cell_count": target_cells,
            "actual_cell_count": actual_cells,
            "actual_area": actual_area,
            "area_error": actual_area - district.area,
            "seed": district.seed,
            "xy_centroid": _xy_centroid(district),
            "neighbor_count": len(district.neighbors),
            "neighbors": list(district.neighbors),
            "expected_density": district.population / district.area,
            "realized_density": district.population / actual_area,
            "street_count": len(district_streets),
            "street_areas": street_areas,
            "min_street_area": min(street_areas),
            "max_street_area": max(street_areas),
            "mean_street_area": sum(street_areas) / len(street_areas),
            "street_prosperities": street_prosperities,
            "weighted_street_prosperity": weighted_prosperity,
        })
    highest = max(districts, key=lambda district: district.prosperity)
    return {
        "seed": seed,
        "target_city_population": target_population,
        "city_template": city_template,
        "actual_total_population": sum(district.population for district in districts),
        "district_count": len(districts),
        "total_hex_cells": sum(len(district.cells) for district in districts),
        "city_center": (0.0, 0.0),
        "hex_cell_area": HEX_CELL_AREA,
        "hex_neighbor_distance": HEX_NEIGHBOR_DISTANCE,
        "bounds": _map_bounds(districts),
        "connected": checks["connected"],
        "overlap_count": checks["overlap_count"],
        "neighbors_symmetric": checks["neighbors_symmetric"],
        "highest_prosperity": {
            "name": highest.name,
            "prosperity": highest.prosperity,
            "seed": highest.seed,
        },
        "districts": rows,
        "streets": [
            {
                "name": street.name,
                "district": street.district,
                "seed": street.seed,
                "cell_count": len(street.cells),
                "area": street.area,
                "centroid": street.centroid,
                "prosperity": street.prosperity,
                "neighbors": list(street.neighbors),
            }
            for street in streets
        ],
    }


def render_map(districts, output_path: Path) -> None:
    min_x, max_x, min_y, max_y = _map_bounds(districts)
    width, height = IMAGE_SIZE
    usable_width = width - 2 * IMAGE_MARGIN
    usable_height = height - 2 * IMAGE_MARGIN
    scale = min(usable_width / (max_x - min_x), usable_height / (max_y - min_y))
    offset_x = (width - (max_x - min_x) * scale) / 2 - min_x * scale
    offset_y = (height - (max_y - min_y) * scale) / 2 + max_y * scale

    def to_pixel(point: tuple[float, float]) -> tuple[float, float]:
        return point[0] * scale + offset_x, offset_y - point[1] * scale

    image = Image.new("RGB", IMAGE_SIZE, (248, 248, 245))
    draw = ImageDraw.Draw(image, "RGBA")
    colors = _district_colors(len(districts))
    for district, color in zip(districts, colors):
        for cell in district.cells:
            polygon = [to_pixel(vertex) for vertex in _hex_vertices(axial_to_xy(cell))]
            draw.polygon(polygon, fill=(*color, 255), outline=(45, 45, 45, 150), width=1)

    label_font = _font(22)
    for district in districts:
        center = to_pixel(_xy_centroid(district))
        label = f"{district.name}\n{district.template}\n{district.level}"
        box = draw.multiline_textbbox(center, label, font=label_font, anchor="mm", align="center", spacing=3)
        padded = (box[0] - 7, box[1] - 5, box[2] + 7, box[3] + 5)
        draw.rounded_rectangle(padded, radius=6, fill=(255, 255, 255, 205), outline=(20, 20, 20, 185), width=1)
        draw.multiline_text(center, label, font=label_font, fill=(10, 10, 10, 255), anchor="mm", align="center", spacing=3)

    center = to_pixel((0.0, 0.0))
    cross = 14
    draw.line((center[0] - cross, center[1], center[0] + cross, center[1]), fill=(0, 0, 0, 255), width=5)
    draw.line((center[0], center[1] - cross, center[0], center[1] + cross), fill=(0, 0, 0, 255), width=5)
    center_font = _font(20)
    draw.text((center[0] + 18, center[1] - 18), "City Center", font=center_font, fill=(0, 0, 0, 255))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG", optimize=True)


def render_street_map(districts, streets, output_path: Path) -> None:
    min_x, max_x, min_y, max_y = _map_bounds(districts)
    width, height = STREET_IMAGE_SIZE
    usable_width = width - 2 * IMAGE_MARGIN
    usable_height = height - 2 * IMAGE_MARGIN
    scale = min(usable_width / (max_x - min_x), usable_height / (max_y - min_y))
    offset_x = (width - (max_x - min_x) * scale) / 2 - min_x * scale
    offset_y = (height - (max_y - min_y) * scale) / 2 + max_y * scale

    def to_pixel(point: tuple[float, float]) -> tuple[float, float]:
        return point[0] * scale + offset_x, offset_y - point[1] * scale

    streets_by_district = defaultdict(list)
    for street in streets:
        streets_by_district[street.district].append(street)
    district_index = {district.name: index for index, district in enumerate(districts)}
    street_colors = {}
    for district in districts:
        local_streets = streets_by_district[district.name]
        base_hue = district_index[district.name] / max(len(districts), 1)
        for index, street in enumerate(local_streets):
            offset = (index - (len(local_streets) - 1) / 2) * .018
            saturation = .48 + .08 * (index % 3)
            value = .78 + .16 * (index / max(len(local_streets) - 1, 1))
            street_colors[street.name] = tuple(
                round(channel * 255)
                for channel in colorsys.hsv_to_rgb((base_hue + offset) % 1.0, saturation, value)
            )

    image = Image.new("RGB", STREET_IMAGE_SIZE, (248, 248, 245))
    draw = ImageDraw.Draw(image, "RGBA")
    for street in streets:
        color = street_colors[street.name]
        for cell in street.cells:
            polygon = [to_pixel(vertex) for vertex in _hex_vertices(axial_to_xy(cell))]
            draw.polygon(polygon, fill=(*color, 255), outline=(45, 45, 45, 105), width=1)

    edge_vertices = ((5, 0), (4, 5), (3, 4), (2, 3), (1, 2), (0, 1))
    for district in districts:
        district_cells = set(district.cells)
        for cell in district.cells:
            vertices = [to_pixel(vertex) for vertex in _hex_vertices(axial_to_xy(cell))]
            for direction, (first, second) in zip(HEX_DIRECTIONS, edge_vertices):
                neighbor = (cell[0] + direction[0], cell[1] + direction[1])
                if neighbor not in district_cells:
                    draw.line((*vertices[first], *vertices[second]), fill=(10, 10, 10, 235), width=5)

    label_font = _font(13)
    for street in streets:
        center = to_pixel(street.centroid)
        label = f"{street.name}\nP{street.prosperity}"
        box = draw.multiline_textbbox(center, label, font=label_font, anchor="mm", align="center", spacing=1)
        draw.rounded_rectangle(
            (box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2),
            radius=3, fill=(255, 255, 255, 195), outline=(25, 25, 25, 130), width=1,
        )
        draw.multiline_text(center, label, font=label_font, fill=(5, 5, 5, 255), anchor="mm", align="center", spacing=1)
        seed_x, seed_y = to_pixel(axial_to_xy(street.seed))
        radius = 5
        draw.ellipse(
            (seed_x - radius, seed_y - radius, seed_x + radius, seed_y + radius),
            fill=(0, 0, 0, 255), outline=(255, 255, 255, 255), width=1,
        )

    center = to_pixel((0.0, 0.0))
    draw.line((center[0] - 14, center[1], center[0] + 14, center[1]), fill=(0, 0, 0, 255), width=5)
    draw.line((center[0], center[1] - 14, center[0], center[1] + 14), fill=(0, 0, 0, 255), width=5)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG", optimize=True)


def _summary_groups(rows: list[dict], key: str) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row[key]].append(row)
    return [
        {
            "key": group,
            "count": len(items),
            "population": sum(item["population"] for item in items),
            "area": sum(item["configured_area"] for item in items),
            "prosperity": sum(item["prosperity"] for item in items) / len(items),
        }
        for group, items in sorted(groups.items())
    ]


def render_report(stats: dict, output_path: Path) -> None:
    bounds = stats["bounds"]
    highest = stats["highest_prosperity"]
    rows = stats["districts"]
    lines = [
        "# City District Map Debug Report",
        "",
        "## Overall summary",
        "",
        f"- RNG seed: `{stats['seed']}`",
        f"- City template: `{stats['city_template']}`",
        f"- Target city population: `{stats['target_city_population']:,}`",
        f"- Actual total population: `{stats['actual_total_population']:,}`",
        f"- District count: `{stats['district_count']}`",
        f"- Total hex cells: `{stats['total_hex_cells']}`",
        f"- City center: `{stats['city_center']}`",
        f"- HEX_CELL_AREA: `{stats['hex_cell_area']}`",
        f"- HEX_NEIGHBOR_DISTANCE: `{stats['hex_neighbor_distance']:.12f}`",
        f"- Map bounds: `({bounds[0]:.4f}, {bounds[1]:.4f}, {bounds[2]:.4f}, {bounds[3]:.4f})`",
        f"- Connected: `{stats['connected']}`",
        f"- Cell overlap count: `{stats['overlap_count']}`",
        f"- Neighbors symmetric: `{stats['neighbors_symmetric']}`",
        f"- Highest prosperity District: `{highest['name']}` / `{highest['prosperity']}` / seed `{highest['seed']}`",
        "",
        "## District details",
        "",
        "| Name | Template | Level | Population | Prosperity | Configured area | Target cells | Actual cells | Actual area | Area error | Seed | XY centroid | Neighbors | Expected density | Realized density |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|---:|",
    ]
    for row in rows:
        neighbors = ", ".join(row["neighbors"]) or "—"
        lines.append(
            f"| {row['name']} | {row['template']} | {row['level']} | {row['population']:,} | "
            f"{row['prosperity']} | {row['configured_area']:.4f} | {row['target_cell_count']} | "
            f"{row['actual_cell_count']} | {row['actual_area']:.4f} | {row['area_error']:+.4f} | "
            f"({row['seed'][0]:.4f}, {row['seed'][1]:.4f}) | "
            f"({row['xy_centroid'][0]:.4f}, {row['xy_centroid'][1]:.4f}) | "
            f"{row['neighbor_count']}: {neighbors} | {row['expected_density']:,.2f} | {row['realized_density']:,.2f} |"
        )

    lines.extend([
        "", "## Street summary by District", "",
        "| District | Street count | Street areas | Minimum area | Maximum area | Mean area | Street prosperities | Area-weighted prosperity |",
        "|---|---:|---|---:|---:|---:|---|---:|",
    ])
    for row in rows:
        area_values = ", ".join(f"{area:.2f}" for area in row["street_areas"])
        prosperity_values = ", ".join(str(value) for value in row["street_prosperities"])
        lines.append(
            f"| {row['name']} | {row['street_count']} | {area_values} | "
            f"{row['min_street_area']:.2f} | {row['max_street_area']:.2f} | "
            f"{row['mean_street_area']:.2f} | {prosperity_values} | "
            f"{row['weighted_street_prosperity']:.3f} |"
        )

    lines.extend([
        "", "## Street details", "",
        "| Name | District | Seed | Cell count | Area | Centroid | Prosperity | Neighbors |",
        "|---|---|---|---:|---:|---|---:|---|",
    ])
    for street in stats["streets"]:
        neighbors = ", ".join(street["neighbors"]) or "—"
        lines.append(
            f"| {street['name']} | {street['district']} | {street['seed']} | "
            f"{street['cell_count']} | {street['area']:.2f} | "
            f"({street['centroid'][0]:.4f}, {street['centroid'][1]:.4f}) | "
            f"{street['prosperity']} | {neighbors} |"
        )

    lines.extend(["", "## By District template", "", "| Template | Count | Population | Configured area | Average prosperity |", "|---|---:|---:|---:|---:|"])
    for group in _summary_groups(rows, "template"):
        lines.append(f"| {group['key']} | {group['count']} | {group['population']:,} | {group['area']:.4f} | {group['prosperity']:.2f} |")

    lines.extend(["", "## By level", "", "| Level | Count | Population | Configured area | Average prosperity |", "|---|---:|---:|---:|---:|"])
    for group in _summary_groups(rows, "level"):
        lines.append(f"| {group['key']} | {group['count']} | {group['population']:,} | {group['area']:.4f} | {group['prosperity']:.2f} |")

    lines.extend(["", "## Configured area ranking", ""])
    for index, row in enumerate(sorted(rows, key=lambda item: item["configured_area"], reverse=True), 1):
        lines.append(f"{index}. {row['name']} — `{row['configured_area']:.4f}`")

    lines.extend(["", "## Realized density ranking", ""])
    for index, row in enumerate(sorted(rows, key=lambda item: item["realized_density"], reverse=True), 1):
        lines.append(f"{index}. {row['name']} — `{row['realized_density']:,.2f}` population/area")

    neighbor_counts = [(row["name"], row["neighbor_count"]) for row in rows]
    lines.extend([
        "", "## Adjacency summary", "",
        f"- Minimum neighbor count: `{min(count for _, count in neighbor_counts)}`",
        f"- Maximum neighbor count: `{max(count for _, count in neighbor_counts)}`",
        "",
    ])
    lines.extend(f"- {name}: `{count}`" for name, count in sorted(neighbor_counts))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_debug_outputs(
    seed: int = DEFAULT_SEED,
    target_population: int = DEFAULT_TARGET_POPULATION,
    city_template: str = DEFAULT_CITY_TEMPLATE,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> tuple[Path, Path, Path, dict]:
    districts, streets = generate_city_map_with_streets(
        target_population, seed, city_template=city_template,
    )
    stats = build_statistics(districts, streets, seed, target_population, city_template)
    image_path = output_dir / "city_map_debug.png"
    street_image_path = output_dir / "city_street_map_debug.png"
    report_path = output_dir / "city_map_debug_report.md"
    render_map(districts, image_path)
    render_street_map(districts, streets, street_image_path)
    render_report(stats, report_path)
    return image_path, street_image_path, report_path, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--target-population", type=int, default=DEFAULT_TARGET_POPULATION)
    parser.add_argument("--city-template", default=DEFAULT_CITY_TEMPLATE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    image_path, street_image_path, report_path, stats = generate_debug_outputs(
        args.seed, args.target_population, args.city_template, args.output_dir,
    )
    print(f"Districts: {stats['district_count']}")
    print(f"Population: {stats['actual_total_population']}")
    print(f"Hex cells: {stats['total_hex_cells']}")
    print(f"Streets: {len(stats['streets'])}")
    print(f"Map: {image_path}")
    print(f"Street map: {street_image_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
