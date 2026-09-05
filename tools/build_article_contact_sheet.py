from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps


CELL_WIDTH = 240
CELL_HEIGHT = 220
LABEL_HEIGHT = 28
DEFAULT_COLUMNS = 5


def build_contact_sheet(
    source_dir: Path,
    output_path: Path,
    *,
    columns: int = DEFAULT_COLUMNS,
) -> Path:
    image_paths = sorted(
        path
        for path in source_dir.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    if not image_paths:
        raise ValueError(f"no images found: {source_dir}")

    rows = (len(image_paths) + columns - 1) // columns
    sheet = Image.new("RGB", (CELL_WIDTH * columns, CELL_HEIGHT * rows), "white")
    draw = ImageDraw.Draw(sheet)

    for index, image_path in enumerate(image_paths):
        column = index % columns
        row = index // columns
        x = column * CELL_WIDTH
        y = row * CELL_HEIGHT
        with Image.open(image_path) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            fitted = ImageOps.contain(
                image,
                (CELL_WIDTH - 12, CELL_HEIGHT - LABEL_HEIGHT - 12),
            )
        image_x = x + (CELL_WIDTH - fitted.width) // 2
        image_y = y + LABEL_HEIGHT + (
            CELL_HEIGHT - LABEL_HEIGHT - fitted.height
        ) // 2
        sheet.paste(fitted, (image_x, image_y))
        draw.rectangle(
            (x, y, x + CELL_WIDTH - 1, y + CELL_HEIGHT - 1),
            outline="#777777",
        )
        draw.text((x + 8, y + 7), image_path.name, fill="black")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, format="JPEG", quality=90, optimize=True)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a labelled contact sheet for an article asset directory."
    )
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_path", type=Path)
    parser.add_argument("--columns", type=int, default=DEFAULT_COLUMNS)
    args = parser.parse_args()
    if args.columns < 1:
        parser.error("--columns must be at least 1")
    print(build_contact_sheet(args.source_dir, args.output_path, columns=args.columns))


if __name__ == "__main__":
    main()
