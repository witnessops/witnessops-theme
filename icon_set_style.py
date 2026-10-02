"""Small, auditable paint changes to editable native KDE Breeze SVGs.

The approved Machined Carbon image is a palette reference, never an icon source.
This module does not create geometry, rasterize artwork, or read installation
state. The caller owns source selection, copying, licensing and provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
import re
import xml.etree.ElementTree as ET


PALETTE = {
    "graphite": "#20282C",
    "graphite_deep": "#141B1F",
    "steel": "#AAB4B9",
    "steel_edge": "#6F7D84",
    "petrol": "#2B616B",
    "teal": "#3D7780",
    "copper_dark": "#825F46",
    "copper_light": "#C3936D",
    "paper": "#BCC4C8",
}
GEOMETRY_POLICY = "Exact native SVG geometry; selected paint properties only."
CATEGORIES = {"places", "mimetypes", "apps", "actions", "devices", "status"}
_SVG = "{http://www.w3.org/2000/svg}"
_ATTR = re.compile(r"([\w:.-]+)\s*=\s*(['\"])(.*?)\2", re.S)
_TOKENS = re.compile(r"<!--.*?-->|<\?.*?\?>|<![^>]*>|<[^>]+>", re.S)
_SOURCE_BLUE = {"#3daee9", "#3593e6", "#147cdc", "#197cf1", "#20bcfa"}
_COLORED_FOLDERS = re.compile(
    r"^folder-(black|blue|brown|cyan|green|gr[ae]y|magenta|orange|red|violet|white|yellow)(?:-|$)"
)
# Native Breeze body colors also supply a deterministic accent when a typed
# color variant uses an accent-role body instead of a literal source color.
_FOLDER_COLOR_ACCENTS = {
    "black": "#333333", "blue": "#4183d7", "brown": "#8b6039",
    "cyan": "#21bbd7", "green": "#3bad7e", "gray": "#a7afb4",
    "grey": "#a7afb4", "magenta": "#b5006a", "orange": "#f89406",
    "red": "#eb0a42", "violet": "#8e44ad", "white": "#e5e7e9",
    "yellow": "#f2cb40",
}
_NEUTRAL_TYPED_BODIES = {
    "folder-encrypted": "#5c5c5c", "folder-decrypted": "#5c5c5c",
    "folder-podcast": "#f89406", "folder-trash": "#3bad7e",
}


def _attrs(tag: str) -> dict[str, str]:
    return {m[1]: m[3] for m in _ATTR.finditer(tag)}


def _attribute(tag: str, name: str, value: str) -> str:
    for match in _ATTR.finditer(tag):
        if match[1] == name:
            return tag[:match.start(3)] + value + tag[match.end(3):]
    position = tag.rfind("/>") if tag.endswith("/>") else tag.rfind(">")
    return tag[:position] + f' {name}="{value}"' + tag[position:]


def _property(attrs: dict[str, str], name: str) -> str | None:
    match = re.search(r"(?:^|;)\s*" + re.escape(name) + r"\s*:\s*([^;]+)", attrs.get("style", ""))
    return match[1].strip() if match else attrs.get(name)


def _paint(tag: str, name: str, value: str) -> str:
    attrs = _attrs(tag)
    style = attrs.get("style", "")
    pattern = re.compile(r"((?:^|;)\s*" + re.escape(name) + r"\s*:\s*)[^;]+")
    if pattern.search(style):
        tag = _attribute(tag, "style", pattern.sub(lambda m: m[1] + value, style))
        # Some native stops carry both a presentation attribute and inline CSS.
        if name in attrs:
            tag = _attribute(tag, name, value)
        return tag
    return _attribute(tag, name, value)


def _patch_tags(text: str, mutate: Callable[[str, dict[str, str]], str]) -> str:
    def replace(match: re.Match[str]) -> str:
        tag = match[0]
        if tag.startswith(("</", "<!", "<?")):
            return tag
        return mutate(tag, _attrs(tag))
    return _TOKENS.sub(replace, text)


def _location(path: str | PurePosixPath, root: ET.Element) -> tuple[str, str, int | None, str]:
    relative = PurePosixPath(path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("SVG provenance path must be relative and remain inside its theme")
    parts = relative.parts
    category = next((part for part in parts if part in CATEGORIES), "")
    size = next((int(m[1]) for part in parts if (m := re.fullmatch(r"(\d+)(?:@\d+x)?", part))), None)
    if size is None:
        box = root.get("viewBox", "").split()
        dimension = box[2] if len(box) == 4 else root.get("width", "").removesuffix("px")
        try:
            size = int(float(dimension))
        except ValueError:
            pass
    return category, relative.stem, size, relative.as_posix()


def transform_svg(data: bytes, relative_path: str | PurePosixPath) -> tuple[bytes, list[dict]]:
    """Return native SVG bytes and stable records of actual paint changes.

    Both ``places/64/folder.svg`` and ``64/places/folder.svg`` are accepted.
    Unselected artwork and small symbolic ``currentColor`` artwork are returned
    byte-for-byte. Geometry, IDs, transforms, namespaces, text and links remain
    untouched; no screenshot or older WitnessOps metadata is consulted.
    """
    if not isinstance(data, bytes):
        raise TypeError("SVG data must be bytes")
    # Unselected artwork is a copy operation, including older branded native
    # files with namespace entities. Parsing or reserializing it is unnecessary.
    category, name, _, _ = _location(relative_path, ET.Element(_SVG + "svg"))
    selected_apps = {"preferences-system", "systemsettings", "org.kde.systemsettings",
                     "utilities-terminal", "konsole", "org.kde.konsole"}
    selected_paper = {"text-plain", "text-x-generic", "application-octet-stream", "unknown"}
    if (not category or category == "status" or name.endswith("-symbolic")
            or (category == "apps" and name not in selected_apps)
            or (category == "mimetypes" and name not in selected_paper)):
        return data, []
    # Breeze includes ordinary <!DOCTYPE svg> declarations. ElementTree does
    # not fetch external DTDs; explicit entity definitions are refused.
    if re.search(br"<!ENTITY", data, re.I):
        raise ValueError("Entity definitions are not supported in native icon sources")
    root = ET.fromstring(data)
    if root.tag != _SVG + "svg":
        raise ValueError("Expected an SVG document")
    text = data.decode("utf-8")
    category, name, size, path = _location(relative_path, root)
    if not category or category == "status" or name.endswith("-symbolic"):
        return data, []
    _, _, intrinsic_size, _ = _location(PurePosixPath(category, "scalable", name + ".svg"), root)
    if "currentColor" in text and any(value is not None and value <= 24 for value in (size, intrinsic_size)):
        return data, []

    records: list[dict] = []

    def record(rule: str, selector: str, before: str, after: str) -> None:
        if before != after:
            records.append({"path": path, "category": category, "rule": rule,
                            "selector": selector, "before": before, "after": after,
                            "geometry_policy": GEOMETRY_POLICY})

    def accent(value: str, rule: str) -> None:
        nonlocal text
        pattern = re.compile(r"(\.ColorScheme-Accent\s*\{[^}]*?\bcolor\s*:\s*)(#[0-9a-fA-F]{6})(?=\s*[;}])")
        def replace(match: re.Match[str]) -> str:
            old = match[2]
            if old.lower() not in _SOURCE_BLUE:
                return match[0]
            record(rule, ".ColorScheme-Accent/color", old, value)
            return match[1] + value
        text = pattern.sub(replace, text)

    if category == "places" and (name in {"folder", "user-home"} or name.startswith(("folder-", "folder_"))):
        color_variant = _COLORED_FOLDERS.match(name)
        paths = root.findall(_SVG + "path")
        if not paths:
            return data, []
        base = paths[0]
        base_fill = _property(base.attrib, "fill")
        literal_color = base_fill is not None and re.fullmatch(r"#[0-9a-fA-F]{6}", base_fill)
        small_colored_base = (color_variant is not None and len(paths) == 1 and literal_color
                              and any(value is not None and value <= 24 for value in (size, intrinsic_size))
                              and _property(base.attrib, "stroke") in {None, "none"})
        if small_colored_base:
            # Small named-color native icons have one compound path, including
            # transparent cutouts. Keep it exact and carry its color on a 1 px
            # outline; the raw path has enough margin to avoid clipping.
            # Black needs a neutral contrast edge on charcoal backgrounds.
            outline_color = PALETTE["steel_edge"] if color_variant[1] == "black" else base_fill
            def small_folder(tag: str, attrs: dict[str, str]) -> str:
                if not re.match(r"<(?:(?:\w+):)?path\b", tag) or attrs.get("d") != base.get("d"):
                    return tag
                changed = _paint(tag, "fill", PALETTE["graphite"])
                changed = _paint(changed, "stroke", outline_color)
                changed = _paint(changed, "stroke-width", "1")
                changed = _paint(changed, "stroke-opacity", "0.95")
                changed = _paint(changed, "stroke-linejoin", "bevel")
                record("native-small-folder-color-outline", "path#" + attrs.get("id", "native"), tag, changed)
                return changed
            result = _patch_tags(text, small_folder).encode("utf-8")
            ET.fromstring(result)
            return result, records
        # This recognizes the native layered folder, not arbitrary branded art.
        native_base = (base.get("class") == "ColorScheme-Accent" and base_fill == "currentColor")
        neutral_typed_base = name in _NEUTRAL_TYPED_BODIES and base_fill == _NEUTRAL_TYPED_BODIES[name]
        # Named color folders have the same native layers, with a literal
        # colored front. Move that color onto their tab and typed glyph rather
        # than retaining a large bright body.
        colored_base = (color_variant is not None and len(paths) >= 4
                        and _property(paths[1].attrib, "fill-opacity") == "0.33"
                        and literal_color)
        # Other literal folder body colors carry vendor or warning identity.
        if not (native_base or neutral_typed_base or colored_base):
            return data, []
        folder_accent = PALETTE["petrol"]
        if color_variant:
            folder_accent = base_fill if colored_base else _FOLDER_COLOR_ACCENTS[color_variant[1]]
        glyph_paint = folder_accent if color_variant else PALETTE["steel"]
        accent(folder_accent, "folder-color-accent" if color_variant else "folder-petrol-tab")
        rear = paths[1] if len(paths) > 1 else None
        rear_d = rear.get("d") if rear is not None and _property(rear.attrib, "fill-opacity") == "0.33" else None
        highlight = paths[2] if len(paths) > 2 else None
        highlight_d = (highlight.get("d") if highlight is not None
                       and _property(highlight.attrib, "fill") == "#ffffff"
                       and _property(highlight.attrib, "fill-opacity") == "0.2" else None)
        outline_width = f"{max(0.75, (intrinsic_size or size or 64) / 40):.3f}".rstrip("0").rstrip(".")

        def folder(tag: str, attrs: dict[str, str]) -> str:
            changed = tag
            if not re.match(r"<(?:(?:\w+):)?path\b", tag):
                return tag
            if attrs.get("d") == base.get("d"):
                # Explicit paint keeps the front graphite even when KDE's
                # global accent is gold. The existing path supplies the edge;
                # there is no new rail, emblem, clipping path or geometry.
                changed = _paint(changed, "fill", PALETTE["graphite"])
                changed = _paint(changed, "stroke", PALETTE["steel"])
                changed = _paint(changed, "stroke-width", outline_width)
                changed = _paint(changed, "stroke-opacity", "0.9")
                changed = _paint(changed, "stroke-linejoin", "bevel")
            if rear_d and attrs.get("d") == rear_d:
                changed = _paint(changed, "fill", folder_accent)
                changed = _paint(changed, "fill-opacity", "0.95")
            if highlight_d and attrs.get("d") == highlight_d:
                changed = _paint(changed, "fill", PALETTE["steel"])
                changed = _paint(changed, "fill-opacity", "0.25")
            if attrs.get("class") == "ColorScheme-Text" and _property(attrs, "fill") == "currentColor" and _property(attrs, "fill-opacity") == "0.6":
                changed = _paint(changed, "fill", glyph_paint)
                changed = _paint(changed, "fill-opacity", "0.9")
            elif attrs.get("class") == "ColorScheme-Text" and _property(attrs, "fill") is None:
                changed = _paint(changed, "fill", PALETTE["graphite_deep"])
            record("native-folder-details", "path#" + attrs.get("id", "native"), tag, changed)
            return changed
        text = _patch_tags(text, folder)

    elif category == "mimetypes":
        # Neutral steel paper only. PDF red, spreadsheet/image green,
        # archives, audio/video, source languages and their native marks stay
        # distinct; none receive a shared gold or teal body.
        def paper(tag: str, attrs: dict[str, str]) -> str:
            if re.match(r"<(?:(?:\w+):)?use\b", tag) and _property(attrs, "fill") in {"#eeeeee", "#eff0f1"}:
                changed = _paint(tag, "fill", PALETTE["paper"])
                record("native-neutral-paper", "use/native-paper", tag, changed)
                return changed
            return tag
        text = _patch_tags(text, paper)

    elif category == "apps" and name in {"preferences-system", "systemsettings", "org.kde.systemsettings"}:
        accent(PALETTE["teal"], "native-settings-rails")
        def knobs(tag: str, attrs: dict[str, str]) -> str:
            if attrs.get("class") == "ColorScheme-Accent" and _property(attrs, "fill") == "currentColor":
                changed = _paint(tag, "fill", PALETTE["teal"])
                record("native-settings-rails", "path#" + attrs.get("id", "native"), tag, changed)
                return changed
            if re.match(r"<(?:(?:\w+):)?circle\b", tag) and _property(attrs, "fill") in {"#fafafa", "#fcfcfc", "#ffffff", "#fff"}:
                changed = _paint(tag, "fill", PALETTE["steel"])
                record("native-settings-knobs", "circle#" + attrs.get("id", "native"), tag, changed)
                return changed
            return tag
        text = _patch_tags(text, knobs)

    elif category == "apps" and name in {"utilities-terminal", "konsole", "org.kde.konsole"}:
        # Native Breeze's `b` gradient paints its chevron. It has no separate
        # cursor at these sizes, so this helper never invents one.
        gradient = re.compile(r"(<linearGradient\b(?=[^>]*\bid=['\"]b['\"])[^>]*>)(.*?)(</linearGradient>)", re.S)
        def copper_gradient(match: re.Match[str]) -> str:
            stops = 0
            def stop(tag: str, attrs: dict[str, str]) -> str:
                nonlocal stops
                if not re.match(r"<stop\b", tag):
                    return tag
                value = PALETTE["copper_dark"] if stops == 0 else PALETTE["copper_light"]
                stops += 1
                old = _property(attrs, "stop-color")
                if old is None or old.lower() not in {"#536161", "#f4f5f5", "#bfc9c9", "#fbfbfb", "#d9dfdf", "#f7f7f7"}:
                    return tag
                changed = _paint(tag, "stop-color", value)
                record("native-terminal-chevron", f"linearGradient#b/stop[{stops}]", tag, changed)
                return changed
            return match[1] + _patch_tags(match[2], stop) + match[3]
        if re.search(r"(?:fill\s*[:=]\s*['\"]?url\(#b\))", text):
            text = gradient.sub(copper_gradient, text)

    elif category in {"actions", "devices"}:
        # Accent role only; semantic Positive/Negative/Neutral text colors and
        # all literal red/green fills remain the native source values.
        accent(PALETTE["teal"], "native-utility-accent")

    result = text.encode("utf-8")
    if result == data:
        return data, []
    ET.fromstring(result)  # Fail before the caller writes an invalid artifact.
    return result, records
