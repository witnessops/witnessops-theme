#!/usr/bin/env python3
"""Read desktop app/icon metadata without retaining command or credential values.

The generated host inventory belongs in .local/, not a distributable package.
Desktop roots are ordered by precedence: first matching desktop ID wins,
including Hidden tombstones. Icon lookup prefers original app artwork in
hicolor, followed by KDE Breeze, and only then other installed themes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable

FIELDS = {"Type", "Name", "Icon", "Hidden", "NoDisplay", "OnlyShowIn", "NotShowIn", "Categories"}
ICON_EXTENSIONS = {".svg", ".svgz", ".png", ".xpm"}


def ordered_unique(paths: Iterable[Path]) -> list[Path]:
    result = []
    seen = set()
    for path in paths:
        key = str(path.expanduser().absolute())
        if key not in seen:
            seen.add(key)
            result.append(Path(key))
    return result


def default_desktop_roots() -> list[Path]:
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    return ordered_unique([
        data_home / "applications",
        home / ".local/share/flatpak/exports/share/applications",
        *(Path(p) / "applications" for p in data_dirs if p),
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ])


def default_icon_roots() -> list[Path]:
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
    data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    return ordered_unique([
        data_home / "icons", home / ".icons",
        home / ".local/share/flatpak/exports/share/icons",
        *(Path(p) / "icons" for p in data_dirs if p),
        Path("/var/lib/flatpak/exports/share/icons"),
        Path("/var/lib/snapd/desktop/icons"),
        Path("/usr/local/share/pixmaps"), Path("/usr/share/pixmaps"),
    ])


def read_metadata(path: Path) -> dict[str, str]:
    """Parse only non-command fields in the Desktop Entry group.

    Localized names and Desktop Actions do not affect this inventory. No Exec,
    TryExec, URL, or action commands are retained or interpreted.
    """
    result: dict[str, str] = {}
    active = False
    # Desktop launchers are tiny. Bound accidental inspection of large files.
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("desktop entry exceeds 1 MiB")
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            line = line.strip()
            if line.startswith("[") and line.endswith("]"):
                active = line == "[Desktop Entry]"
                continue
            if not active or not line or line.startswith("#"):
                continue
            equals = line.find("=")
            if equals >= 0 and line[:equals] in FIELDS:
                result[line[:equals]] = line[equals + 1:]
    return result


def unescape(value: str) -> str:
    replacements = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    return re.sub(r"\\([sntr\\])", lambda m: replacements[m.group(1)], value)


def normalize_icon_key(desktop_id: str, icon: str) -> str:
    # Theme lookup accepts an icon name, not an absolute path. Absolute launchers
    # get a safe stable key for a separately receipted local desktop override.
    name = desktop_id.removesuffix(".desktop") if Path(icon).is_absolute() else icon
    if Path(name).suffix.lower() in ICON_EXTENSIONS:
        name = str(Path(name).with_suffix(""))
    name = re.sub(r"[^A-Za-z0-9_.+-]", "-", name).strip(".-")
    return name or re.sub(r"[^A-Za-z0-9_.+-]", "-", desktop_id.removesuffix(".desktop"))


def app_category(desktop_id: str, name: str, icon: str, categories: str = "") -> str:
    """Give custom renderers a modest palette hint while retaining app identity."""
    tokens = re.sub(r"[^a-z0-9]+", " ", f"{desktop_id} {name} {icon}".lower())
    groups = [
        ("security", ("keepass", "yubico", "authenticator", "credential", "password", "wallet")),
        ("development", ("codium", "code", "codex", "kate", "vim", "python", "qtcreator")),
        ("files", ("dolphin", "ark", "nextcloud", "obsidian", "okular", "blackbox", "black box", "hotkeys")),
        ("communication", ("thunderbird", "betterbird", "mail", "chatgpt", "slack", "signal", "teams")),
        ("media", ("spotify", "vlc", "gwenview", "spectacle", "imagemagick", "audacity", "gimp", "inkscape")),
        ("internet", ("chrome", "chromium", "firefox", "browser", "remmina", "remote", "network")),
        ("system", ("konsole", "terminal", "tuxedo", "system", "calculator", "kcalc", "virt manager", "gparted", "discover")),
    ]
    for category, needles in groups:
        if any(needle in tokens for needle in needles):
            return category
    for token, category in [("Development", "development"), ("AudioVideo", "media"), ("Graphics", "media"), ("Network", "internet"), ("Office", "files"), ("System", "system"), ("Utility", "system")]:
        if token in categories.split(";"):
            return category
    return "applications"


class IconResolver:
    def __init__(self, roots: Iterable[Path]):
        self.roots = ordered_unique(roots)
        self.index: dict[str, list[tuple[tuple, Path]]] = {}
        for root_index, root in enumerate(self.roots):
            if not root.is_dir():
                continue
            # Do not follow directory symlinks into unbounded trees. Individual
            # artwork symlinks are accepted and resolved to their actual file.
            for directory, folders, names in os.walk(root, followlinks=False):
                folders.sort()
                for filename in sorted(names):
                    path = Path(directory) / filename
                    if path.suffix.lower() not in ICON_EXTENSIONS:
                        continue
                    relative = path.relative_to(root)
                    parts = relative.parts
                    theme = parts[0].lower() if len(parts) > 1 else ""
                    if theme == "hicolor":
                        theme_rank = 0
                    elif theme == "breeze":
                        theme_rank = 1
                    elif theme == "breeze-dark":
                        theme_rank = 2
                    elif "witnessops" in theme:
                        theme_rank = 5
                    else:
                        theme_rank = 3
                    # Prefer full-color application assets and vector/high-res
                    # files rather than a small monochrome toolbar rendering.
                    context_rank = 0 if "apps" in parts else 1
                    extension_rank = {".svg": 0, ".svgz": 1, ".png": 2, ".xpm": 3}[path.suffix.lower()]
                    sizes = [int(m.group(1)) for p in parts if (m := re.match(r"^(\d+)(?:x\d+)?$", p))]
                    size_rank = -max(sizes, default=256)
                    score = (theme_rank, context_rank, extension_rank, root_index, size_rank, str(relative))
                    self.index.setdefault(path.stem, []).append((score, path))
        for candidates in self.index.values():
            candidates.sort(key=lambda candidate: candidate[0])

    def resolve(self, icon: str) -> tuple[Path | None, str | None]:
        if not icon:
            return None, None
        if Path(icon).is_absolute():
            path = Path(icon)
            return (path.resolve(), "absolute") if path.is_file() else (None, "absolute")
        key = Path(icon).stem if Path(icon).suffix.lower() in ICON_EXTENSIONS else icon
        for _, path in self.index.get(key, []):
            if path.is_file():
                return path.resolve(), "theme"
        return None, None


def collect_inventory(desktop_roots: Iterable[Path], icon_roots: Iterable[Path], desktops: Iterable[str] = ("KDE",)) -> dict:
    desktop_roots = ordered_unique(desktop_roots)
    resolver = IconResolver(icon_roots)
    current_desktops = set(desktops)
    chosen = {}
    duplicates = []
    errors = []
    scanned = 0
    for root in desktop_roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.desktop")):
            desktop_id = "-".join(path.relative_to(root).parts)
            scanned += 1
            if desktop_id in chosen:
                duplicates.append({"desktop_id": desktop_id, "desktop_file": str(path), "selected_file": str(chosen[desktop_id][0])})
                continue
            try:
                chosen[desktop_id] = (path, read_metadata(path))
            except (OSError, ValueError) as exc:
                errors.append({"desktop_id": desktop_id, "desktop_file": str(path), "error": type(exc).__name__})
                # An unreadable higher-precedence entry cannot justify falling
                # through to an older system launcher.
                chosen[desktop_id] = (path, {})

    apps = []
    hidden_entries = []
    missing_sources = []
    for desktop_id, (path, metadata) in sorted(chosen.items()):
        only = set(filter(None, metadata.get("OnlyShowIn", "").split(";")))
        excluded = set(filter(None, metadata.get("NotShowIn", "").split(";")))
        if metadata.get("Hidden", "").lower() == "true":
            reason = "Hidden"
        elif metadata.get("NoDisplay", "").lower() == "true":
            reason = "NoDisplay"
        elif metadata.get("Type") != "Application":
            reason = "NotApplication"
        elif only and not only.intersection(current_desktops):
            reason = "OnlyShowIn"
        elif excluded.intersection(current_desktops):
            reason = "NotShowIn"
        else:
            reason = None
        if reason:
            hidden_entries.append({"desktop_id": desktop_id, "name": unescape(metadata.get("Name", "")), "icon": metadata.get("Icon", ""), "desktop_file": str(path), "reason": reason})
            continue
        name = unescape(metadata.get("Name", desktop_id.removesuffix(".desktop")))
        icon = unescape(metadata.get("Icon", ""))
        source, source_kind = resolver.resolve(icon)
        row = {
            "desktop_id": desktop_id, "name": name, "icon": icon,
            "icon_key": normalize_icon_key(desktop_id, icon),
            "absolute_icon": Path(icon).is_absolute(),
            "desktop_file": str(path), "desktop_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source": str(source) if source else None,
            "source_kind": source_kind,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source else None,
            "category": app_category(desktop_id, name, icon, metadata.get("Categories", "")),
        }
        apps.append(row)
        if not source:
            missing_sources.append({"desktop_id": desktop_id, "name": name, "icon": icon})
    reasons = {reason: sum(row["reason"] == reason for row in hidden_entries) for reason in sorted({row["reason"] for row in hidden_entries})}
    return {
        "schema_version": 1,
        "theme_identity": "WitnessOpsIconsV1_0",
        "desktop_roots": [str(root) for root in desktop_roots],
        "icon_roots": [str(root) for root in resolver.roots],
        "desktops": sorted(current_desktops),
        "counts": {"desktop_files_scanned": scanned, "effective_entries": len(chosen), "apps": len(apps), "hidden_entries": len(hidden_entries), "excluded_by_reason": reasons, "shadowed_entries": len(duplicates), "absolute_icons": sum(row["absolute_icon"] for row in apps), "missing_sources": len(missing_sources), "errors": len(errors)},
        "apps": apps, "hidden_entries": hidden_entries,
        "shadowed_entries": duplicates, "missing_sources": missing_sources,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Private JSON inventory output; use .local/ on the host")
    parser.add_argument("--desktop-root", action="append", type=Path, help="Override desktop roots in priority order; repeat per root")
    parser.add_argument("--icon-root", action="append", type=Path, help="Override artwork roots; repeat per root")
    parser.add_argument("--desktop", action="append", help="Desktop environment identifiers; defaults to XDG_CURRENT_DESKTOP or KDE")
    args = parser.parse_args(argv)
    desktops = args.desktop or list(filter(None, os.environ.get("XDG_CURRENT_DESKTOP", "KDE").split(":")))
    inventory = collect_inventory(args.desktop_root or default_desktop_roots(), args.icon_root or default_icon_roots(), desktops)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output.absolute()), "counts": inventory["counts"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
