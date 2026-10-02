"""Regression checks for authored app identity, native fallback and lookup order."""

import configparser
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtGui import QIcon, QImage
from PyQt5.QtWidgets import QApplication

import build_icon_set


NATIVE_INDEX = """[Icon Theme]
Name=Breeze Dark
Name[pl]=Bryza
Comment=Native icons
Comment[de]=Originale Symbole
Inherits=breeze,hicolor
Directories=apps/16,apps/48,places/64
ScaledDirectories=apps/16@2x

[apps/16]
Size=16
Type=Fixed
Context=Applications

[apps/48]
Size=48
Type=Fixed
Context=Applications

[places/64]
Size=64
Type=Fixed
Context=Places

[apps/16@2x]
Size=16
Scale=2
Type=Fixed
Context=Applications
"""


def colored_svg(color):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" '
            f'viewBox="0 0 48 48"><path fill="{color}" d="M8 8H40V40H8Z"/></svg>').encode()


def symbolic_svg(size):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
            f'viewBox="0 0 {size} {size}"><style>.ColorScheme-Text {{color:#eff0f1;}}</style>'
            '<path class="ColorScheme-Text" style="fill:currentColor" '
            'd="M2 2L8 6L2 10V8L5 6L2 4Z"/></svg>').encode()


def parse_index(text):
    result = configparser.ConfigParser(interpolation=None)
    result.optionxform = str
    result.read_string(text)
    return result


class IconSetBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Hold the application for the full test class so Qt's source cache is
        # exercised across consecutive renderings, as it is in a real build.
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "public-theme"
        self.package.mkdir()
        native_index = self.root / "native-index.theme"
        native_index.write_text(NATIVE_INDEX)
        (self.package / "index.theme").write_text(build_icon_set.index_text(native_index))

    def asset(self, name, data):
        path = self.package / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def row(self, key, source):
        return {"desktop_id": key + ".desktop", "desktop_file": str(self.root / (key + ".desktop")),
                "icon_key": key, "source": str(source), "absolute_icon": False}

    def output(self, name="rendered"):
        output = self.root / name
        output.mkdir()
        return output

    def test_consecutive_special_apps_keep_distinct_artwork_despite_qicon_cache(self):
        terminal = self.asset("apps/48/utilities-terminal.svg", colored_svg("#ec0000"))
        settings = self.asset("apps/48/preferences-system.svg", colored_svg("#0000ec"))
        output = self.output()
        rows = [self.row("utilities-terminal", terminal), self.row("preferences-system", settings)]
        build_icon_set.render_apps(self.package, output, rows, {})
        red = QImage(str(output / "local/48/apps/utilities-terminal.png"))
        blue = QImage(str(output / "local/48/apps/preferences-system.png"))
        self.assertFalse(red.isNull())
        self.assertFalse(blue.isNull())
        self.assertEqual(red.pixelColor(24, 24).getRgb(), (236, 0, 0, 255))
        self.assertEqual(blue.pixelColor(24, 24).getRgb(), (0, 0, 236, 255),
                         "A shared mutable SVG pathname must not make QIcon reuse another app's artwork")
        self.assertEqual(list(output.glob(".render-source*.svg")), [], "Transient render sources must be removed")

    def test_native_small_terminal_symbolic_svgs_survive_private_overlay(self):
        large = self.asset("apps/48/utilities-terminal.svg", colored_svg("#ec0000"))
        native = {size: symbolic_svg(size) for size in (16, 24)}
        for size, data in native.items():
            self.asset(f"apps/{size}/utilities-terminal.svg", data)
        output = self.output()
        reports = build_icon_set.render_apps(self.package, output, [self.row("utilities-terminal", large)], {})
        for size, original in native.items():
            with self.subTest(size=size):
                symbolic = output / f"local/{size}/apps/utilities-terminal.svg"
                self.assertTrue(symbolic.is_file(), "Small currentColor utility artwork must retain its native SVG")
                self.assertEqual(symbolic.read_bytes(), original)
                self.assertFalse((symbolic.with_suffix(".png")).exists(),
                                 "A PNG override would mask native symbolic colors and geometry")
                self.assertIn(symbolic.relative_to(output).as_posix(), reports[0]["paths"])
        self.assertTrue((output / "local/48/apps/utilities-terminal.png").is_file())

    def test_index_has_identity_inheritance_and_a_section_for_every_directory(self):
        parsed = parse_index((self.package / "index.theme").read_text())
        theme = parsed["Icon Theme"]
        self.assertEqual(theme["Name"], "WitnessOps Icons v1.0")
        self.assertEqual(theme["Inherits"], "breeze-dark,hicolor")
        self.assertNotIn("Name[pl]", theme)
        self.assertNotIn("Comment[de]", theme)
        directories = theme["Directories"].split(",")
        scaled = theme["ScaledDirectories"].split(",")
        self.assertEqual(len(directories), len(set(directories)))
        for directory in directories + scaled:
            self.assertIn(directory, parsed.sections())
            self.assertGreater(int(parsed[directory]["Size"]), 0)
            self.assertEqual(parsed[directory]["Type"], "Fixed")
        for size in (16, 24, 32, 48, 64, 128, 256):
            section = parsed[f"local/{size}/apps"]
            self.assertEqual(int(section["Size"]), size)
            self.assertEqual(section["Context"], "Applications")

    def test_native_inventory_excludes_undeclared_trees_and_root_icons_but_keeps_hidpi_aliases(self):
        native = self.root / "native"
        expected = ("apps/16/example.svg", "apps/16@2x/example.svg",
                    "apps/48/example.svg", "places/64/folder.svg")
        for name in ("apps/16/example.svg", "apps/48/example.svg", "places/64/folder.svg",
                     "undeclared/48/private.svg", "apps/96/unlisted.svg", "root.svg",
                     "apps/48/unexpected/nested.svg"):
            path = native / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(colored_svg("#20282c"))
        (native / "apps/16@2x").symlink_to("16", target_is_directory=True)
        (native / "index.theme").write_text(NATIVE_INDEX)
        observed = [path.relative_to(native).as_posix() for path in build_icon_set.native_paths(native)]
        self.assertEqual(observed, sorted(expected))
        self.assertTrue((native / "apps/16@2x/example.svg").is_file())
        self.assertEqual((native / "apps/16@2x/example.svg").read_bytes(),
                         (native / "apps/16/example.svg").read_bytes())

    def test_native_inventory_refuses_unsafe_declared_directories(self):
        native = self.root / "native"
        native.mkdir()
        source = native / "index.theme"
        for directory in ("../outside", "/outside"):
            with self.subTest(directory=directory):
                source.write_text(NATIVE_INDEX.replace("apps/16,apps/48,places/64", directory))
                with self.assertRaisesRegex(ValueError, "Unsafe native theme directory"):
                    build_icon_set.native_paths(native)

    def test_empty_native_directories_are_pruned_without_losing_populated_hidpi_aliases(self):
        native = self.root / "native"
        (native / "apps/16").mkdir(parents=True)
        (native / "apps/48").mkdir(parents=True)
        (native / "places/64").mkdir(parents=True)
        (native / "apps/16/example.svg").write_bytes(symbolic_svg(16))
        (native / "places/64/folder.svg").write_bytes(colored_svg("#20282c"))
        (native / "apps/16@2x").symlink_to("16", target_is_directory=True)
        source = native / "index.theme"
        source.write_text(NATIVE_INDEX)
        parsed = parse_index(build_icon_set.index_text(source, source_root=native))
        directories = parsed["Icon Theme"]["Directories"].split(",")
        self.assertIn("apps/16", directories)
        self.assertIn("places/64", directories)
        self.assertNotIn("apps/48", directories)
        self.assertNotIn("apps/48", parsed)
        self.assertEqual(parsed["Icon Theme"]["ScaledDirectories"], "apps/16@2x")
        self.assertIn("apps/16@2x", parsed)
        for size in build_icon_set.SIZES:
            self.assertIn(f"local/{size}/apps", directories)
            self.assertIn(f"local/{size}/apps", parsed)

    def test_custom_artwork_bypasses_small_native_svg_and_accent_transformer(self):
        large = self.asset("apps/48/preferences-system.svg", colored_svg("#ec0000"))
        sixteen, twenty_two = symbolic_svg(16), symbolic_svg(22)
        self.asset("apps/16/preferences-system.svg", sixteen)
        self.asset("apps/22/preferences-system.svg", twenty_two)
        custom = self.root / "custom-settings.svg"
        custom.write_bytes(b'''<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 48 48">
<style>.ColorScheme-Accent {color:#3daee9;}</style>
<path class="ColorScheme-Accent" style="fill:currentColor" d="M8 8H40V40H8Z"/></svg>''')
        output = self.output()
        for size in (16, 24):
            target = output / f"local/{size}/apps/preferences-system.svg"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(symbolic_svg(size))
        build_icon_set.render_apps(self.package, output, [self.row("preferences-system", large)],
                                  {"preferences-system": custom})
        for size in (16, 24, 48):
            with self.subTest(size=size):
                self.assertFalse((output / f"local/{size}/apps/preferences-system.svg").exists(),
                                 "An obsolete native SVG must not survive beside the authored replacement")
                expected = QIcon(str(custom)).pixmap(size, size).toImage()
                rendered = QImage(str(output / f"local/{size}/apps/preferences-system.png"))
                self.assertEqual(rendered.pixelColor(size // 2, size // 2).getRgb(),
                                 expected.pixelColor(size // 2, size // 2).getRgb(),
                                 "Custom art must retain its chosen appearance at every size")

    def test_authored_terminal_aliases_keep_design_png_at_small_and_large_sizes(self):
        original = self.asset("apps/48/utilities-terminal.svg", colored_svg("#ec0000"))
        for size in (16, 24):
            self.asset(f"apps/{size}/utilities-terminal.svg", symbolic_svg(size))
        aliases = ("konsole", "org.kde.konsole", "witnessops-ai-cli")
        authored_svg = self.root / "approved-cli.svg"
        authored_svg.write_bytes(colored_svg("#0000ec"))
        authored_png = self.root / "approved-cli.png"
        self.assertTrue(QIcon(str(authored_svg)).pixmap(256, 256).save(str(authored_png), "PNG"))
        output = self.output()
        build_icon_set.render_apps(self.package, output, [self.row(key, original) for key in aliases],
                                  {key: authored_png for key in aliases})
        for key in aliases:
            for size in (16, 24, 48):
                with self.subTest(key=key, size=size):
                    self.assertFalse((output / f"local/{size}/apps/{key}.svg").exists())
                    self.assertEqual(QImage(str(output / f"local/{size}/apps/{key}.png"))
                                     .pixelColor(size // 2, size // 2).getRgb(), (0, 0, 236, 255),
                                     "Small launcher icons must keep the approved design instead of stock geometry")

    def test_authored_settings_asset_maps_to_actual_launcher_and_settings_aliases(self):
        expected_aliases = {"preferences-system", "systemsettings", "org.kde.systemsettings"}
        self.assertEqual(set(build_icon_set.AUTHORED["witnessops-settings.png"]), expected_aliases)
        authoring = self.root / "authoring"
        authoring.mkdir()
        original = self.asset("apps/48/preferences-system.svg", colored_svg("#ec0000"))
        for size in (16, 24):
            self.asset(f"apps/{size}/preferences-system.svg", symbolic_svg(size))
        approved = self.root / "approved-settings.svg"
        approved.write_bytes(colored_svg("#0000ec"))
        settings_png = authoring / "witnessops-settings.png"
        self.assertTrue(QIcon(str(approved)).pixmap(256, 256).save(str(settings_png), "PNG"))
        output = self.output()
        with patch.object(build_icon_set, "ASSETS", authoring):
            custom = build_icon_set.authored_assets()
        self.assertEqual(set(custom), expected_aliases)
        build_icon_set.render_apps(self.package, output,
                                  [self.row(key, original) for key in sorted(expected_aliases)], custom)
        for key in expected_aliases:
            for size in (16, 24, 48):
                with self.subTest(key=key, size=size):
                    self.assertFalse((output / f"local/{size}/apps/{key}.svg").exists())
                    image = QImage(str(output / f"local/{size}/apps/{key}.png"))
                    self.assertEqual(image.pixelColor(size // 2, size // 2).getRgb(), (0, 0, 236, 255))

    def test_browser_wizard_vscodium_and_screenshot_have_dedicated_authored_keys(self):
        self.assertEqual(build_icon_set.AUTHORED["witnessops-blackbox.png"], ("witnessops-blackbox",))
        self.assertEqual(build_icon_set.AUTHORED["witnessops-blackbox-browser.png"], ("blackbox-browser",))
        self.assertEqual(build_icon_set.AUTHORED["witnessops-credential-import.png"], ("witnessops-credential-import",))
        self.assertEqual(build_icon_set.AUTHORED["witnessops-vscodium.png"], ("com.vscodium.codium",))
        self.assertEqual(build_icon_set.AUTHORED["witnessops-screenshot.png"], ("spectacle",))
        self.assertNotIn("dialog-password", build_icon_set.AUTHORED_KEYS,
                         "The wizard's custom mark must not replace unrelated password dialogs")

    def test_private_browser_wizard_vscodium_and_screenshot_use_distinct_art_without_global_password_override(self):
        authoring = self.root / "authoring"
        authoring.mkdir()
        colors = {"witnessops-blackbox.png": "#ec0000", "witnessops-blackbox-browser.png": "#0000ec",
                  "witnessops-credential-import.png": "#00ec00", "witnessops-vscodium.png": "#00ecec",
                  "witnessops-screenshot.png": "#ec00ec"}
        for filename, color in colors.items():
            vector = authoring / (filename + ".svg")
            vector.write_bytes(colored_svg(color))
            self.assertTrue(QIcon(str(vector)).pixmap(256, 256).save(str(authoring / filename), "PNG"))
        original = self.asset("apps/48/dialog-password.svg", colored_svg("#ecec00"))
        original_bytes = original.read_bytes()
        wizard = self.row("dialog-password", original)
        wizard["desktop_id"] = build_icon_set.WIZARD_DESKTOP_ID
        rows = [self.row("witnessops-blackbox", original), self.row("blackbox-browser", original),
                wizard, self.row("dialog-password", original), self.row("com.vscodium.codium", original),
                self.row("spectacle", original)]
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps({"apps": rows, "missing_sources": [], "errors": []}))
        output = self.root / "private-overlay"
        with patch.object(build_icon_set, "ASSETS", authoring):
            report = build_icon_set.local_package(self.package, output, inventory)
        self.assertEqual(next(row for row in report["apps"] if row["desktop_id"] == build_icon_set.WIZARD_DESKTOP_ID)
                         ["icon_key"], build_icon_set.WIZARD_ICON_KEY)
        expected = {"witnessops-blackbox": (236, 0, 0, 255), "blackbox-browser": (0, 0, 236, 255),
                    "witnessops-credential-import": (0, 236, 0, 255), "dialog-password": (236, 236, 0, 255),
                    "com.vscodium.codium": (0, 236, 236, 255), "spectacle": (236, 0, 236, 255)}
        for key, color in expected.items():
            for size in (16, 24, 48):
                with self.subTest(key=key, size=size):
                    self.assertEqual(QImage(str(output / f"local/{size}/apps/{key}.png"))
                                     .pixelColor(size // 2, size // 2).getRgb(), color)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual((output / "apps/48/dialog-password.svg").read_bytes(), original_bytes)

    def test_local_overlay_precedes_native_directories_and_reports_exact_assets(self):
        original = self.asset("apps/48/chat-app.svg", colored_svg("#0000ec"))
        native_before = original.read_bytes()
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps({"apps": [self.row("chat-app", original)],
                                         "missing_sources": [], "errors": []}))
        output = self.root / "private-overlay"
        report = build_icon_set.local_package(self.package, output, inventory)
        index = parse_index((output / "index.theme").read_text())
        directories = index["Icon Theme"]["Directories"].split(",")
        local = [f"local/{size}/apps" for size in (16, 24, 32, 48, 64, 128, 256)]
        self.assertEqual(directories[:len(local)], local)
        self.assertEqual(len(directories), len(set(directories)))
        self.assertEqual((output / "apps/48/chat-app.svg").read_bytes(), native_before)
        self.assertEqual(original.read_bytes(), native_before)
        self.assertEqual(report["visible_launchers"], 1)
        self.assertEqual(report["unique_icon_keys"], 1)
        self.assertEqual(report["missing_sources"], [])
        for path in report["apps"][0]["paths"]:
            self.assertTrue((output / path).is_file())
            self.assertTrue(path.startswith("local/"))
        self.assertEqual(index["Icon Theme"]["Inherits"], "breeze-dark,hicolor")

    def test_private_overlay_preserves_already_authored_public_terminal_identity(self):
        original = self.asset("apps/48/utilities-terminal.svg", colored_svg("#ec0000"))
        for size in (16, 24):
            self.asset(f"apps/{size}/utilities-terminal.svg", symbolic_svg(size))
            self.asset(f"local/{size}/apps/utilities-terminal.svg", symbolic_svg(size))
        authored = self.root / "approved-cli.svg"
        authored.write_bytes(colored_svg("#0000ec"))
        # An older public delivery contains authored 32+ assets and native tiny
        # SVGs. A fresh private overlay must keep the design at every size and
        # remove the obsolete tiny SVGs copied with that older publication.
        for size in (32, 48, 64, 128, 256):
            target = self.package / f"local/{size}/apps/utilities-terminal.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(QIcon(str(authored)).pixmap(size, size).save(str(target), "PNG"))
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps({"apps": [self.row("utilities-terminal", original)],
                                         "missing_sources": [], "errors": []}))
        output = self.root / "private-overlay"
        empty_assets = self.root / "absent-authoring-assets"
        with patch.object(build_icon_set, "ASSETS", empty_assets):
            build_icon_set.local_package(self.package, output, inventory)
        self.assertEqual(QImage(str(output / "local/48/apps/utilities-terminal.png")).pixelColor(24, 24).getRgb(),
                         (0, 0, 236, 255), "Private app rendering must retain the approved public CLI artwork")
        for size in (16, 24):
            self.assertFalse((output / f"local/{size}/apps/utilities-terminal.svg").exists())
            self.assertEqual((self.package / f"local/{size}/apps/utilities-terminal.svg").read_bytes(),
                             symbolic_svg(size), "Building the private copy must preserve the original public package")
            self.assertEqual(QImage(str(output / f"local/{size}/apps/utilities-terminal.png"))
                             .pixelColor(size // 2, size // 2).getRgb(), (0, 0, 236, 255))

    def test_private_overlay_preserves_exact_packaged_authored_pngs_at_every_size(self):
        original = self.asset("apps/48/preferences-system.svg", colored_svg("#ec0000"))
        approved = self.root / "approved-settings.svg"
        approved.write_bytes(colored_svg("#0000ec"))
        expected = {}
        for size in build_icon_set.SIZES:
            target = self.package / f"local/{size}/apps/preferences-system.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            self.assertTrue(QIcon(str(approved)).pixmap(size, size).save(str(target), "PNG"))
            expected[size] = target.read_bytes()
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps({"apps": [self.row("preferences-system", original)],
                                         "missing_sources": [], "errors": []}))
        output = self.root / "private-overlay"
        with patch.object(build_icon_set, "ASSETS", self.root / "absent-authoring-assets"):
            build_icon_set.local_package(self.package, output, inventory)
        for size, data in expected.items():
            with self.subTest(size=size):
                self.assertEqual((output / f"local/{size}/apps/preferences-system.png").read_bytes(), data)
                self.assertFalse((output / f"local/{size}/apps/preferences-system.svg").exists())

    def test_private_native_chatgpt_svg_keeps_chosen_art_at_all_sizes(self):
        original = self.asset("apps/48/chatgpt.svg", colored_svg("#ec0000"))
        for size in (16, 24):
            self.asset(f"apps/{size}/chatgpt.svg", symbolic_svg(size))
        private_art = self.root / ".local/icon-set-v1.0-artwork/chatgpt.svg"
        private_art.parent.mkdir(parents=True)
        private_art.write_bytes(colored_svg("#0000ec"))
        inventory = self.root / "inventory.json"
        inventory.write_text(json.dumps({"apps": [self.row("chatgpt", original)],
                                         "missing_sources": [], "errors": []}))
        output = self.root / "private-overlay"
        with patch.object(build_icon_set, "ROOT", self.root), \
                patch.object(build_icon_set, "ASSETS", self.root / "absent-authoring-assets"):
            build_icon_set.local_package(self.package, output, inventory)
        for size in build_icon_set.SIZES:
            with self.subTest(size=size):
                image = QImage(str(output / f"local/{size}/apps/chatgpt.png"))
                self.assertEqual(image.pixelColor(size // 2, size // 2).getRgb(), (0, 0, 236, 255))
                self.assertFalse((output / f"local/{size}/apps/chatgpt.svg").exists())

    @unittest.skipUnless(build_icon_set.PACKAGE.is_dir() and (build_icon_set.NATIVE / "index.theme").is_file(),
                         "built publication package and native KDE sources are unavailable")
    def test_public_package_materializes_native_scaled_directory_aliases(self):
        native = parse_index((build_icon_set.NATIVE / "index.theme").read_text())
        scaled = native["Icon Theme"].get("ScaledDirectories", "").split(",")
        aliases = [name for name in scaled if name and (build_icon_set.NATIVE / name).is_dir()
                   and any(candidate.is_file() for candidate in (build_icon_set.NATIVE / name).glob("*.svg"))]
        missing = [name for name in aliases if not (build_icon_set.PACKAGE / name).is_dir()]
        self.assertEqual(missing, [],
                         "Advertised native HiDPI directories must exist; a nearest-size fallback can change symbolic artwork")
        for name in aliases:
            with self.subTest(directory=name):
                target = build_icon_set.PACKAGE / name
                self.assertFalse(target.is_symlink(), "Installed packages must materialize native aliases")
                representative = next((build_icon_set.NATIVE / name).glob("*.svg"), None)
                if representative is None:
                    continue
                self.assertTrue((target / representative.name).is_file())
                native_directory = (build_icon_set.NATIVE / name).resolve()
                if native_directory.is_relative_to(build_icon_set.NATIVE):
                    equivalent = build_icon_set.PACKAGE / native_directory.relative_to(build_icon_set.NATIVE) / representative.name
                    self.assertEqual((target / representative.name).read_bytes(), equivalent.read_bytes())


if __name__ == "__main__":
    unittest.main()
