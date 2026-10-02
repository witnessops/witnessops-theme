import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inventory_icons", ROOT / "tools/inventory_icons.py")
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


class IconInventoryTest(unittest.TestCase):
    def test_hidden_user_entry_suppresses_system_application(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            user, system = root / "user", root / "system"
            user.mkdir()
            system.mkdir()
            (user / "example.desktop").write_text("[Desktop Entry]\nHidden=true\n")
            (system / "example.desktop").write_text("[Desktop Entry]\nType=Application\nName=Example\nIcon=example\nExec=secret-must-not-be-retained\n")
            (system / "helper.desktop").write_text("[Desktop Entry]\nType=Application\nName=Helper\nNoDisplay=true\n")
            result = INVENTORY.collect_inventory([user, system], [])
            self.assertEqual(result["apps"], [])
            self.assertEqual(result["counts"]["shadowed_entries"], 1)
            self.assertEqual(result["counts"]["excluded_by_reason"], {"Hidden": 1, "NoDisplay": 1})
            self.assertNotIn("secret-must-not-be-retained", str(result))

    def test_absolute_icon_resolves_actual_file_and_safe_key(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apps = root / "apps"
            apps.mkdir()
            artwork = root / "genuine.svg"
            artwork.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            link = root / "linked.svg"
            link.symlink_to(artwork)
            (apps / "com.example.App.desktop").write_text(f"[Desktop Entry]\nType=Application\nName=Example\nIcon={link}\nExec=not-in-inventory\n")
            result = INVENTORY.collect_inventory([apps], [])
            app = result["apps"][0]
            self.assertEqual(app["source"], str(artwork.resolve()))
            self.assertEqual(app["source_kind"], "absolute")
            self.assertEqual(app["icon_key"], "com.example.App")
            self.assertTrue(app["absolute_icon"])
            self.assertEqual(result["missing_sources"], [])
            self.assertNotIn("not-in-inventory", str(result))

    def test_hicolor_identity_preferred_and_desktop_visibility_respected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            apps = root / "apps"
            icons = root / "icons"
            apps.mkdir()
            for theme in ("hicolor", "breeze"):
                target = icons / theme / "scalable/apps"
                target.mkdir(parents=True)
                (target / "example.svg").write_text(f"<svg>{theme}</svg>")
            (apps / "visible.desktop").write_text("[Desktop Entry]\nType=Application\nName=Example\nIcon=example\nOnlyShowIn=KDE;\n")
            (apps / "other.desktop").write_text("[Desktop Entry]\nType=Application\nName=Other\nOnlyShowIn=GNOME;\n")
            result = INVENTORY.collect_inventory([apps], [icons], ["KDE"])
            self.assertEqual(len(result["apps"]), 1)
            self.assertIn("/hicolor/", result["apps"][0]["source"])
            self.assertEqual(result["counts"]["excluded_by_reason"], {"OnlyShowIn": 1})


if __name__ == "__main__":
    unittest.main()
