"""Native paint policy and source-integrity tests for the complete icon set."""

from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from icon_set_style import PALETTE, transform_svg


SVG = "{http://www.w3.org/2000/svg}"
BREEZE = Path("/usr/share/icons/breeze")
# Native Breeze layered folder structure; the path strings are deliberately
# varied here to ensure the transformer preserves source-defined geometry.
FOLDER = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><style>.ColorScheme-Text {color:#232629;} .ColorScheme-Accent {color:#3daee9;}</style></defs>
<path class="ColorScheme-Accent" style="fill:currentColor" d="M4 6L4 20L3 20L3 58L61 58L61 16L60 16L60 10L32 10L28 6Z"/>
<path style="fill-opacity:0.33" d="M4 6L4 20L22 20L26 16L60 16L60 10L32 10L28 6Z"/>
<path class="ColorScheme-Text" style="fill-opacity:0.2" d="M3 57L3 58L61 58L61 57Z"/>
<path class="ColorScheme-Text" style="fill:currentColor;fill-opacity:0.6" d="M23 27L23 47L35 47L41 41L41 27Z"/>
</svg>'''


def geometry(data):
    """All source structure and attributes except explicitly editable paint."""
    editable = {"style", "fill", "fill-opacity", "stop-color", "stroke", "stroke-width",
                "stroke-opacity", "stroke-linejoin"}
    result = []
    for element in ET.fromstring(data).iter():
        attributes = {key: value for key, value in element.attrib.items() if key not in editable}
        styles = dict(declaration.strip().split(":", 1) for declaration in element.get("style", "").split(";") if ":" in declaration)
        styles = {key.strip(): value.strip() for key, value in styles.items() if key.strip() not in editable}
        result.append((element.tag, attributes, styles))
    return result


class IconSetStyleTest(unittest.TestCase):
    def test_folder_geometry_and_native_emblem_are_preserved(self):
        result, records = transform_svg(FOLDER, "places/64/folder-documents.svg")
        self.assertEqual(geometry(result), geometry(FOLDER))
        self.assertIn(PALETTE["graphite"].encode(), result)
        self.assertIn(PALETTE["petrol"].encode(), result)
        self.assertIn(PALETTE["steel"].encode(), result)
        paths = ET.fromstring(result).findall(SVG + "path")
        self.assertIn("fill:" + PALETTE["graphite"], paths[0].get("style"))
        self.assertEqual(paths[0].get("stroke"), PALETTE["steel"])
        self.assertEqual(float(paths[0].get("stroke-width")), 1.6)
        self.assertEqual(paths[0].get("stroke-opacity"), "0.9")
        self.assertEqual(paths[0].get("stroke-linejoin"), "bevel")
        self.assertIn("fill-opacity:0.95", paths[1].get("style"))
        for old in (b"#D4B06A", b"#F1EADF", b"#358EB4", b"#64A8AC"):
            self.assertNotIn(old, result)
        self.assertTrue(records)
        self.assertTrue(all(record["path"] == "places/64/folder-documents.svg" for record in records))

    def test_idempotency_and_path_layouts(self):
        result, records = transform_svg(FOLDER, "64/places/folder-documents.svg")
        repeated, second_records = transform_svg(result, "64/places/folder-documents.svg")
        self.assertEqual(result, repeated)
        self.assertEqual(second_records, [])
        self.assertEqual((result, records), transform_svg(FOLDER, "64/places/folder-documents.svg"))

    def test_small_symbolic_currentcolor_is_byte_preserved(self):
        for category in ("places", "apps", "actions", "devices", "mimetypes", "status"):
            for size in (16, 22, 24):
                self.assertEqual(transform_svg(FOLDER, f"{category}/{size}/folder.svg"), (FOLDER, []))
        small_source = FOLDER.replace(b'viewBox="0 0 64 64"', b'viewBox="0 0 16 16"')
        self.assertEqual(transform_svg(small_source, "places/48/folder.svg"), (small_source, []))

    def test_plain_doctype_and_unmapped_native_entity_artwork_are_retained(self):
        source = b"<!DOCTYPE svg>\n" + FOLDER
        result, records = transform_svg(source, "places/64/folder.svg")
        self.assertTrue(result.startswith(b"<!DOCTYPE svg>\n"))
        self.assertTrue(records)
        branded = b'<!DOCTYPE svg [<!ENTITY svguri "http://www.w3.org/2000/svg">]><svg xmlns="&svguri;"/>'
        self.assertEqual(transform_svg(branded, "apps/48/ktouch.svg"), (branded, []))

    def test_semantic_colors_and_branded_apps_are_untouched(self):
        semantic = b'''<svg xmlns="http://www.w3.org/2000/svg" width="64">
<style>.ColorScheme-Accent {color:#3daee9;} .ColorScheme-NegativeText {color:#da4453;} .ColorScheme-PositiveText {color:#27ae60;}</style>
<path fill="#da2c2c" d="M1 1L4 1L4 4Z"/><path fill="#87d37c" d="M8 8L9 8L9 9Z"/></svg>'''
        result, _ = transform_svg(semantic, "actions/64/document-save.svg")
        for color in (b"#da4453", b"#27ae60", b"#da2c2c", b"#87d37c"):
            self.assertIn(color, result)
        for path in ("mimetypes/64/application-pdf.svg", "mimetypes/64/image-x-generic.svg", "apps/64/spotify.svg", "status/64/dialog-error.svg"):
            self.assertEqual(transform_svg(semantic, path), (semantic, []))

    def test_neutral_paper_uses_steel_without_repainting_type_marks(self):
        source = b'''<svg xmlns="http://www.w3.org/2000/svg" width="64"><defs><path id="paper" d="M10 3L40 3L54 17L54 61Z"/></defs><use fill="#eeeeee" href="#paper"/><path fill="#27ae60" d="M21 22L43 22L43 23Z"/></svg>'''
        result, _ = transform_svg(source, "mimetypes/64/text-plain.svg")
        self.assertEqual(geometry(result), geometry(source))
        self.assertIn(PALETTE["paper"].encode(), result)
        self.assertIn(b"#27ae60", result)

    def test_color_coded_folders_keep_accents_on_shared_material_and_native_geometry(self):
        colors = {"black": "#333333", "blue": "#4183d7", "brown": "#8b6039", "cyan": "#21bbd7",
                  "green": "#3bad7e", "grey": "#a7afb4", "magenta": "#b5006a", "orange": "#f89406",
                  "red": "#eb0a42", "violet": "#8e44ad", "white": "#e5e7e9", "yellow": "#f2cb40"}
        for color, accent in colors.items():
            for suffix in ("", "-documents", "-music", "-open"):
                with self.subTest(color=color, suffix=suffix):
                    source = FOLDER.replace(b'class="ColorScheme-Accent" style="fill:currentColor"',
                                            f'style="fill:{accent}"'.encode(), 1)
                    result, records = transform_svg(source, f"places/64/folder-{color}{suffix}.svg")
                    self.assertEqual(geometry(result), geometry(source))
                    paths = ET.fromstring(result).findall(SVG + "path")
                    self.assertIn("fill:" + PALETTE["graphite"], paths[0].get("style"))
                    self.assertEqual(paths[0].get("stroke"), PALETTE["steel"])
                    self.assertEqual(paths[1].get("fill"), accent)
                    self.assertIn("fill:" + accent, paths[-1].get("style"))
                    self.assertTrue(records)
                    self.assertEqual(transform_svg(result, f"places/64/folder-{color}{suffix}.svg"), (result, []))
        # Native accent-role typed variants use the name's stable color when
        # there is no literal colored body from which to extract it.
        result, _ = transform_svg(FOLDER, "places/64/folder-blue-documents.svg")
        self.assertEqual(ET.fromstring(result).findall(SVG + "path")[1].get("fill"), colors["blue"])

    def test_vendor_and_semantic_marks_inside_a_typed_folder_retain_literal_paint(self):
        source = FOLDER.replace(b"</svg>", b'<path id="vendor" fill="#ff6600" d="M30 30H34V34H30Z"/><path class="ColorScheme-NegativeText" fill="#da4453" d="M4 4H8V8H4Z"/></svg>')
        for path in ("places/64/folder-vendor.svg", "places/64/folder-blue-documents.svg"):
            with self.subTest(path=path):
                result, records = transform_svg(source, path)
                self.assertEqual(geometry(result), geometry(source))
                parsed = ET.fromstring(result)
                self.assertEqual(parsed.find(SVG + "path[@id='vendor']").get("fill"), "#ff6600")
                self.assertIn(b"#da4453", result)
                self.assertTrue(records)

    def test_selected_utility_accents_use_petrol_steel_and_copper(self):
        settings = b'''<svg xmlns="http://www.w3.org/2000/svg" width="48"><style>.ColorScheme-Accent {color:#3daee9;}</style><path class="ColorScheme-Accent" fill="currentColor" d="M2 2H30V3H2Z"/><circle fill="#fafafa" cx="8" cy="3" r="2"/></svg>'''
        result, records = transform_svg(settings, "apps/48/preferences-system.svg")
        self.assertEqual(geometry(result), geometry(settings))
        self.assertIn(PALETTE["teal"].encode(), result)
        self.assertIn(PALETTE["steel"].encode(), result)
        self.assertTrue(records)
        terminal = b'''<svg xmlns="http://www.w3.org/2000/svg" width="48"><defs><linearGradient id="b"><stop stop-color="#536161"/><stop stop-color="#f4f5f5" offset="1"/></linearGradient></defs><path fill="url(#b)" d="M2 2L12 12L2 22Z"/></svg>'''
        result, records = transform_svg(terminal, "apps/48/utilities-terminal.svg")
        self.assertEqual(geometry(result), geometry(terminal))
        self.assertIn(PALETTE["copper_dark"].encode(), result)
        self.assertIn(PALETTE["copper_light"].encode(), result)
        self.assertTrue(records)

    def test_neutral_typed_literal_bodies_and_legacy_html_folder_are_consistent(self):
        for name, color in (("folder-decrypted", "#5c5c5c"), ("folder-podcast", "#f89406"),
                            ("folder-trash", "#3bad7e")):
            with self.subTest(name=name):
                source = FOLDER.replace(b'class="ColorScheme-Accent" style="fill:currentColor"',
                                        ('style="fill:' + color + '"').encode(), 1)
                result, records = transform_svg(source, "places/64/" + name + ".svg")
                self.assertEqual(geometry(result), geometry(source))
                self.assertIn(PALETTE["graphite"].encode(), result)
                self.assertIn(PALETTE["petrol"].encode(), result)
                self.assertTrue(records)
        self.assertTrue(transform_svg(FOLDER, "places/64/folder_html.svg")[1])

    def test_invalid_document_and_escaping_paths(self):
        for source, path in ((b"<svg", "places/64/folder.svg"), (FOLDER, "../places/64/folder.svg"), (FOLDER, "/places/64/folder.svg")):
            with self.assertRaises((ValueError, ET.ParseError)):
                transform_svg(source, path)
        source = FOLDER.replace(b"<svg", b'<!DOCTYPE svg [<!ENTITY x "unexpected">]><svg', 1)
        with self.assertRaises(ValueError):
            transform_svg(source, "places/64/folder.svg")

    @unittest.skipUnless((BREEZE / "places/64/folder.svg").is_file(), "native KDE Breeze sources are unavailable")
    def test_actual_native_sources_parse_and_preserve_geometry(self):
        paths = ["places/64/folder.svg", "places/64/folder-documents.svg", "places/64/folder-downloads.svg", "places/64/folder-pictures.svg", "places/64/folder-music.svg", "places/64/folder-videos.svg", "places/64/folder-publicshare.svg", "places/64/folder-templates.svg", "places/64/folder-remote.svg", "places/64/folder-open.svg", "places/64/folder-encrypted.svg", "places/64/folder-decrypted.svg", "places/64/folder-podcast.svg", "places/64/folder-trash.svg", "places/64/folder_html.svg", "apps/48/utilities-terminal.svg", "apps/32/preferences-system.svg", "apps/48/preferences-system.svg", "mimetypes/64/text-plain.svg", "mimetypes/64/application-pdf.svg", "mimetypes/64/image-x-generic.svg"]
        for path in paths:
            with self.subTest(path=path):
                source = (BREEZE / path).read_bytes()
                result, records = transform_svg(source, path)
                self.assertEqual(geometry(source), geometry(result))
                self.assertEqual(transform_svg(result, path), (result, []))
                if path.startswith("places/") or "preferences-system" in path or "utilities-terminal" in path or "text-plain" in path:
                    self.assertTrue(records, path)
                else:
                    self.assertEqual(source, result)

    @unittest.skipUnless((BREEZE / "places/64/folder.svg").is_file(), "native KDE Breeze sources are unavailable")
    def test_native_vendor_and_warning_folder_colors_are_byte_preserved(self):
        for name in ("folder-android", "folder-blender", "folder-deb", "folder-java", "folder-rpm",
                     "folder-crash", "folder-important"):
            with self.subTest(name=name):
                path = "places/64/" + name + ".svg"
                self.assertEqual(transform_svg((BREEZE / path).read_bytes(), path),
                                 ((BREEZE / path).read_bytes(), []))

    @unittest.skipUnless((BREEZE / "places/64/folder-red.svg").is_file(), "native KDE Breeze sources are unavailable")
    def test_native_colored_folders_use_graphite_fronts_and_keep_source_color_tabs(self):
        for color in ("black", "blue", "brown", "cyan", "green", "grey", "magenta", "orange", "red", "violet", "yellow"):
            for size in (16, 22, 32, 48, 64, 96):
                with self.subTest(color=color, size=size):
                    path = f"places/{size}/folder-{color}.svg"
                    source = (BREEZE / path).read_bytes()
                    result, records = transform_svg(source, path)
                    self.assertEqual(geometry(result), geometry(source))
                    original = ET.fromstring(source).findall(SVG + "path")
                    native_color = dict(part.split(":", 1) for part in original[0].get("style").split(";") if ":" in part)["fill"]
                    paths = ET.fromstring(result).findall(SVG + "path")
                    base_style = dict(part.split(":", 1) for part in paths[0].get("style").split(";") if ":" in part)
                    self.assertEqual(base_style["fill"], PALETTE["graphite"])
                    if size <= 24:
                        self.assertEqual(len(paths), 1)
                        outline_color = PALETTE["steel_edge"] if color == "black" else native_color
                        self.assertEqual(base_style.get("stroke", paths[0].get("stroke")), outline_color)
                        self.assertEqual(float(paths[0].get("stroke-width")), 1)
                        self.assertEqual(paths[0].get("stroke-linejoin"), "bevel")
                    else:
                        self.assertEqual(base_style.get("stroke", paths[0].get("stroke")), PALETTE["steel"])
                        self.assertEqual(paths[1].get("fill"), native_color)
                        self.assertEqual(float(paths[0].get("stroke-width")), size / 40)
                    self.assertTrue(records)
                    self.assertEqual(transform_svg(result, path), (result, []))

    @unittest.skipUnless((BREEZE / "places/16/folder-black.svg").is_file(), "native KDE Breeze sources are unavailable")
    def test_small_black_folder_has_a_legible_neutral_edge(self):
        def luminance(color):
            rgb = [int(color[offset:offset + 2], 16) / 255 for offset in (1, 3, 5)]
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
            return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
        for size in (16, 22):
            with self.subTest(size=size):
                path = f"places/{size}/folder-black.svg"
                source = (BREEZE / path).read_bytes()
                result, records = transform_svg(source, path)
                self.assertEqual(geometry(result), geometry(source))
                body = ET.fromstring(result).find(SVG + "path")
                self.assertEqual(body.get("stroke"), PALETTE["steel_edge"])
                self.assertIn("fill:" + PALETTE["graphite"], body.get("style"))
                contrast = (luminance(body.get("stroke")) + 0.05) / (luminance(PALETTE["graphite"]) + 0.05)
                self.assertGreater(contrast, 3)
                self.assertTrue(records)

    @unittest.skipUnless((BREEZE / "places/64/user-home.svg").is_file(), "native KDE Breeze sources are unavailable")
    def test_native_home_folder_material_and_symbolic_house_are_consistent(self):
        for size in (16, 22, 32, 48, 64, 96):
            with self.subTest(size=size):
                path = f"places/{size}/user-home.svg"
                source = (BREEZE / path).read_bytes()
                result, records = transform_svg(source, path)
                if size <= 24:
                    self.assertEqual((result, records), (source, []))
                    continue
                self.assertEqual(geometry(result), geometry(source))
                paths = ET.fromstring(result).findall(SVG + "path")
                self.assertIn("fill:" + PALETTE["graphite"], paths[0].get("style"))
                base_style = dict(part.split(":", 1) for part in paths[0].get("style", "").split(";") if ":" in part)
                self.assertEqual(base_style.get("stroke", paths[0].get("stroke")), PALETTE["steel"])
                self.assertEqual(float(paths[0].get("stroke-width")), max(0.75, size / 40))
                self.assertEqual(paths[0].get("stroke-opacity"), "0.9")
                self.assertEqual(paths[0].get("stroke-linejoin"), "bevel")
                self.assertEqual(paths[1].get("fill"), PALETTE["petrol"])
                self.assertIn("fill:" + PALETTE["steel"], paths[-1].get("style"))
                self.assertEqual(paths[-1].get("d"), ET.fromstring(source).findall(SVG + "path")[-1].get("d"))
                self.assertTrue(records)
                self.assertEqual(transform_svg(result, path), (result, []))


if __name__ == "__main__":
    unittest.main()
