#!/usr/bin/env python3
"""Render a private review sheet from the actual packaged v1.0 icon files.

Qt renders every selected SVG/PNG at its requested native size. The adjacent
JSON records exact lookup paths, hashes, rendered bounds, inherited fallbacks,
and missing artwork. This renderer never selects the host's desktop icon theme.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile

from PIL import Image, ImageDraw, ImageFont


FOLDERS = [
    ("Folder", "folder"), ("Open", "folder-open"), ("Home", "user-home"),
    ("Documents", "folder-documents"), ("Downloads", "folder-downloads"),
    ("Pictures", "folder-pictures"), ("Music", "folder-music"),
    ("Videos", "folder-videos"), ("Development", "folder-development"),
    ("Work", "folder-work"), ("Git", "folder-git"),
    ("Shared", "folder-publicshare"), ("Encrypted", "folder-encrypted"),
    ("Gold", "folder-yellow"), ("Teal", "folder-cyan"),
    ("Blue", "folder-blue"), ("Green", "folder-green"),
    ("Red", "folder-red"), ("Violet", "folder-violet"),
]
MIMES = [
    ("PDF", "application-pdf"), ("Text", "text-plain"),
    ("Markdown", "text-markdown"), ("Source", "text-x-python"),
    ("JSON", "application-json"), ("Spreadsheet", "x-office-spreadsheet"),
    ("Document", "x-office-document"), ("Archive", "application-zip"),
    ("Audio", "audio-x-generic"), ("Video", "video-x-generic"),
    ("Image", "image-x-generic"), ("Executable", "application-x-executable"),
]
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")


def read_index(theme: Path) -> configparser.ConfigParser:
    value = configparser.ConfigParser(interpolation=None, strict=False)
    value.read(theme / "index.theme", encoding="utf-8")
    if not value.has_section("Icon Theme"):
        raise ValueError("Icon theme index is missing")
    return value


def directory_distance(section, size: int) -> int:
    base = section.getint("Size", fallback=48)
    scale = section.getint("Scale", fallback=1)
    if scale != 1:
        return 100000
    kind = section.get("Type", "Threshold").lower()
    if kind == "fixed":
        return abs(base - size)
    if kind == "scalable":
        minimum = section.getint("MinSize", fallback=base)
        maximum = section.getint("MaxSize", fallback=base)
    else:
        threshold = section.getint("Threshold", fallback=2)
        minimum, maximum = base - threshold, base + threshold
    return max(minimum - size, size - maximum, 0)


def lookup(theme: Path, icon_name: str, size: int, roots: list[Path], visited: tuple[Path, ...] = ()) -> tuple[Path | None, Path | None]:
    if not NAME.fullmatch(icon_name) or ".." in icon_name:
        raise ValueError("Unsafe icon lookup name")
    theme = theme.absolute()
    if theme in visited or len(visited) > 12 or not (theme / "index.theme").is_file():
        return None, None
    index = read_index(theme)
    directories = index["Icon Theme"].get("Directories", "").split(",")
    choices = []
    for order, directory in enumerate(directories):
        directory = directory.strip()
        if not directory or directory not in index or Path(directory).is_absolute() or ".." in Path(directory).parts:
            continue
        distance = directory_distance(index[directory], size)
        if distance >= 100000:
            continue
        for extension_order, extension in enumerate((".png", ".svg", ".svgz", ".xpm")):
            source = theme / directory / (icon_name + extension)
            if source.is_file():
                choices.append(((distance, order, extension_order), source))
    if choices:
        source = min(choices, key=lambda item: item[0])[1]
        return source.resolve(), theme
    for inherited in index["Icon Theme"].get("Inherits", "").split(","):
        inherited = inherited.strip()
        if not inherited or not NAME.fullmatch(inherited) or ".." in inherited:
            continue
        for root in roots:
            source, source_theme = lookup(root / inherited, icon_name, size, roots, (*visited, theme))
            if source is not None:
                return source, source_theme
    return None, None


def font(size: int):
    for path in (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")):
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def wrap_label(draw, text: str, face, width: int) -> list[str]:
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip()
        if current and draw.textlength(candidate, font=face) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    result = lines[:2]
    if len(lines) > 2:
        result[-1] += "…"
    for index, line in enumerate(result):
        while line and draw.textlength(line, font=face) > width:
            line = line[:-2] + "…" if len(line) > 2 else "…"
        result[index] = line
    return result


def render_sheet(theme: Path, inventory: Path, output: Path) -> dict:
    # These settings affect only this diagnostic process. No global theme API,
    # desktop configuration, GTK settings, or session command is invoked.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QT_QPA_PLATFORMTHEME"] = ""
    os.environ["QT_STYLE_OVERRIDE"] = "Fusion"
    os.environ["QT_SCALE_FACTOR"] = "1"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
    from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QSize
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import QApplication

    theme = theme.expanduser().absolute()
    index = read_index(theme)
    value = json.loads(inventory.read_text(encoding="utf-8"))
    apps = [(row["name"], row["icon_key"], row["desktop_id"]) for row in value["apps"]]
    roots = [theme.parent, Path("/usr/share/icons"), Path("/usr/local/share/icons")]
    # A running Codex build can have no standard desktop launcher. Include its
    # supplemental artwork only if the exact package actually supplies it.
    if lookup(theme, "codex", 48, [theme.parent])[0] is not None:
        apps.append(("Codex", "codex", "supplemental:codex"))
    sections = [
        ("Applications · 48 px", apps, 48, 8, 116),
        ("Folders and types · 48 px", [(label, key, None) for label, key in FOLDERS], 48, 8, 105),
        ("File types · 48 px", [(label, key, None) for label, key in MIMES], 48, 8, 105),
        ("Applications · native 32 px", apps, 32, 11, 94),
    ]
    width = 1440
    height = 124 + sum(44 + math.ceil(len(items) / columns) * row_height + 18 for _, items, _, columns, row_height in sections)
    canvas = Image.new("RGB", (width, height), "#10171b")
    draw = ImageDraw.Draw(canvas)
    title_font, heading_font, label_font, small_font = font(27), font(18), font(13), font(12)
    draw.text((30, 25), "WitnessOps Icons v1.0", fill="#f1e9d4", font=title_font)
    draw.text((30, 66), "Actual packaged artwork · Qt rendering · native pixel sizes", fill="#9dafa9", font=label_font)
    draw.text((30, 89), "Small gold dot = explicit inherited fallback. Red cross = missing or empty render.", fill="#9dafa9", font=small_font)
    records, missing, inherited = [], [], []
    with tempfile.TemporaryDirectory(prefix="witnessops-icon-preview-") as temporary:
        os.chmod(temporary, 0o700)
        previous_runtime = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = temporary
        application = QApplication.instance() or QApplication(["preview-icon-set", "-platform", "offscreen"])
        y = 124
        for title, items, size, columns, row_height in sections:
            draw.text((30, y), title, fill="#ead8a4", font=heading_font)
            y += 39
            cell_width = (width - 60) // columns
            for item_index, (label, key, desktop_id) in enumerate(items):
                column, row = item_index % columns, item_index // columns
                x = 30 + column * cell_width
                top = y + row * row_height
                center = x + cell_width // 2
                source, source_theme = lookup(theme, key, size, roots)
                record = {"section": title, "name": label, "icon_key": key, "desktop_id": desktop_id, "size": size, "source": str(source) if source else None, "source_theme": str(source_theme) if source_theme else None, "inherited": source_theme is not None and source_theme != theme, "sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source else None}
                rendered = None
                if source is not None:
                    pixmap = QIcon(str(source)).pixmap(QSize(size, size))
                    if not pixmap.isNull():
                        byte_array = QByteArray()
                        buffer = QBuffer(byte_array)
                        buffer.open(QIODevice.WriteOnly)
                        pixmap.save(buffer, "PNG")
                        buffer.close()
                        rendered = Image.open(io.BytesIO(bytes(byte_array))).convert("RGBA")
                        record["rendered_size"] = list(rendered.size)
                        bounds = rendered.getchannel("A").getbbox()
                        record["render_bounds"] = list(bounds) if bounds else None
                        record["visible_pixels"] = sum(pixel > 0 for pixel in rendered.getchannel("A").getdata())
                        if bounds is None:
                            rendered = None
                if rendered is None:
                    record["status"] = "missing_or_empty"
                    missing.append({"icon_key": key, "size": size, "section": title})
                    draw.line((center - 12, top + 8, center + 12, top + 32), fill="#df6c67", width=2)
                    draw.line((center - 12, top + 32, center + 12, top + 8), fill="#df6c67", width=2)
                else:
                    record["status"] = "rendered"
                    canvas.paste(rendered, (center - rendered.width // 2, top + (48 - rendered.height) // 2), rendered)
                    if record["inherited"]:
                        inherited.append({"icon_key": key, "size": size, "source_theme": str(source_theme)})
                        draw.ellipse((center + 25, top + 2, center + 30, top + 7), fill="#d8b869")
                lines = wrap_label(draw, label, label_font, cell_width - 18)
                for line_index, line in enumerate(lines):
                    text_width = draw.textlength(line, font=label_font)
                    draw.text((center - text_width / 2, top + 58 + line_index * 17), line, fill="#dddcd3", font=label_font)
                records.append(record)
            y += math.ceil(len(items) / columns) * row_height + 18
        application.processEvents()
        if previous_runtime is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = previous_runtime
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    report = {"schema_version": 1, "theme": str(theme), "theme_name": index["Icon Theme"].get("Name"), "inventory": str(inventory.absolute()), "image": str(output.absolute()), "counts": {"visible_launchers": len(value["apps"]), "application_samples": len(apps), "rendered_samples": sum(record["status"] == "rendered" for record in records), "missing_samples": len(missing), "inherited_samples": len(inherited)}, "missing": missing, "inherited": inherited, "records": records}
    output.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--theme", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    report = render_sheet(args.theme, args.inventory, args.output)
    print(json.dumps({"image": report["image"], "report": str(args.output.with_suffix('.json').absolute()), "counts": report["counts"]}, sort_keys=True))
    return 1 if report["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
