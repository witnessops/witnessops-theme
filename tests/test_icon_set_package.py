"""Standalone archive boundaries, integrity and repeatability checks."""

from pathlib import Path
import gzip
import hashlib
import io
import json
import struct
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zlib

import pack_icon_set as package
from build_icon_set import prune_empty_icon_directories


def png(size, color=(24, 30, 36, 255)):
    """A real RGBA PNG fixture, independent of the desktop renderer."""
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    pixels = (b"\0" + bytes(color) * size) * size
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class IconSetPackageTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in package.FILE_ALLOWLIST:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x89PNG\r\n\x1a\nfixture" if name.endswith(".png") else b"allowlisted source\n")
        # The packer is trusted independently of the root being packaged. Other
        # miniature code fixtures are pinned once before adversarial mutations.
        (self.root / "pack_icon_set.py").write_bytes(Path(package.__file__).read_bytes())
        for name in package.OPTIONAL_FILES:
            if name.endswith(".py"):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"# optional public Python fixture\n")
        self.release_code_pins = dict(package.V1_CODE_DIGESTS)
        code_pins = patch.dict(package.V1_CODE_DIGESTS, {
            name: package.digest((self.root / name).read_bytes())
            for name in self.release_code_pins
        })
        code_pins.start()
        self.addCleanup(code_pins.stop)
        self.theme = self.root / package.THEME_DIRECTORY
        local_directories = [f"local/{size}/apps" for size in (16, 24, 32, 48, 64, 128, 256)]
        for directory in ("apps/48", "apps/16@2x", *local_directories):
            (self.theme / directory).mkdir(parents=True, exist_ok=True)
        (self.theme / "index.theme").write_text(
            "[Icon Theme]\nName=WitnessOps Icons v1.0\nInherits=breeze-dark,hicolor\n"
            "Directories=apps/48," + ",".join(local_directories) + "\nScaledDirectories=apps/16@2x\n\n"
            "[apps/48]\nSize=48\n[apps/16@2x]\nSize=16\nScale=2\n"
            + "".join(f"[{directory}]\nSize={directory.split('/')[1]}\n" for directory in local_directories))
        self.svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48"/>'
        for name in ("example", "second"):
            (self.theme / f"apps/48/{name}.svg").write_bytes(self.svg)
        for name in ("COPYRIGHT-BREEZE", "COPYING-BREEZE-ICONS"):
            (self.theme / name).write_text("Breeze license fixture\n")
        provenance = {"theme_identity": package.THEME, "version": package.VERSION,
                      "file_count": 2, "records": [{"path": f"apps/48/{name}.svg", "sha256": package.digest(self.svg)}
                                                   for name in ("example", "second")]}
        (self.root / "assets/icon-set-v1.0/PROVENANCE.json").write_text(json.dumps(provenance))
        (self.root / "assets/icon-set-v1.0/folder-aliases.json").write_text(
            json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": []}))
        self.release_pins = dict(package.V1_NATIVE_MANIFEST_DIGESTS)
        # Bind the miniature release once. Repacked or mutated metadata cannot update it.
        pins = patch.dict(package.V1_NATIVE_MANIFEST_DIGESTS, {})
        pins.start()
        self.addCleanup(pins.stop)
        self.pin_fixture_inventory()
        self.artwork = self.root / "assets/icon-set-v1.0/artwork.json"
        artwork = {"schema_version": 1, "theme_identity": package.THEME, "version": package.VERSION,
                   "assets": [], "rendered": []}
        # Independent names make accidental omissions in the production allowlist visible.
        sources = {
            "witnessops-ai-cli.png": ("utilities-terminal", "konsole", "org.kde.konsole", "witnessops-ai-cli"),
            "witnessops-blackbox.png": ("witnessops-blackbox",),
            "witnessops-settings.png": ("preferences-system", "systemsettings", "org.kde.systemsettings"),
            "witnessops-blackbox-browser.png": ("blackbox-browser",),
            "witnessops-credential-import.png": ("witnessops-credential-import",),
            "witnessops-vscodium.png": ("com.vscodium.codium",),
            "witnessops-screenshot.png": ("spectacle",),
        }
        for number, (filename, keys) in enumerate(sources.items()):
            source_name = "assets/icon-set-v1.0/" + filename
            source_data = png(256, (24 + number * 12, 30, 36, 255))
            (self.root / source_name).write_bytes(source_data)
            artwork["assets"].append({"path": source_name, "sha256": package.digest(source_data), "icon_keys": list(keys)})
            for size in (16, 24, 32, 48, 64, 128, 256):
                rendered_data = png(size, (24 + number * 12, 30, 36, 255))
                for key in keys:
                    relative = f"local/{size}/apps/{key}.png"
                    (self.theme / relative).write_bytes(rendered_data)
                    artwork["rendered"].append({"path": relative, "sha256": package.digest(rendered_data)})
        self.artwork.write_text(json.dumps(artwork))
        self.release_artwork_pin = package.V1_ARTWORK_MANIFEST_DIGEST
        artwork_pin = patch.object(package, "V1_ARTWORK_MANIFEST_DIGEST",
                                   package.digest(self.artwork.read_bytes()))
        artwork_pin.start()
        self.addCleanup(artwork_pin.stop)
        self.release_metadata_pins = dict(package.V1_RELEASE_METADATA_DIGESTS)
        # Approve the miniature guide and index once. Rewritten source/archive
        # metadata cannot redefine the trusted fixture after this setup.
        metadata_pins = patch.dict(package.V1_RELEASE_METADATA_DIGESTS, {
            name: package.digest((self.root / name).read_bytes())
            for name in self.release_metadata_pins
        })
        metadata_pins.start()
        self.addCleanup(metadata_pins.stop)
        for name in (".local/private.svg", ".git/history", "packages/user-v1/old.svg", "README.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must be excluded")

    def pin_fixture_inventory(self):
        """Declare trusted fixture bytes before adversarial archive mutations."""
        for name in package.V1_NATIVE_MANIFEST_DIGESTS:
            package.V1_NATIVE_MANIFEST_DIGESTS[name] = package.digest((self.root / name).read_bytes())

    def native_asset(self, relative, data):
        path = self.theme / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        provenance_path = self.root / "assets/icon-set-v1.0/PROVENANCE.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["records"].append({"path": relative, "sha256": package.digest(data)})
        provenance["file_count"] = len(provenance["records"])
        provenance_path.write_text(json.dumps(provenance))
        self.pin_fixture_inventory()

    def folder_aliases(self):
        records = []
        for alias, canonical in (("folder-work", "folder-documents"), ("folder-projects", "folder-development")):
            source = f"places/64/{canonical}.svg"
            target = f"places/64/{alias}.svg"
            data = self.svg.replace(b'width="48"', b'width="64"').replace(
                b"/>", b"><title>" + canonical.encode() + b"</title></svg>")
            self.native_asset(source, data)
            (self.theme / target).write_bytes(data)
            records.append({"path": target, "source": source,
                            "source_sha256": package.digest(data), "sha256": package.digest(data)})
        metadata = self.root / "assets/icon-set-v1.0/folder-aliases.json"
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": records}))
        self.pin_fixture_inventory()
        return metadata, records

    def test_allowlist_manifest_and_empty_scaled_directories(self):
        output = self.root / "one.tar.gz"
        result = package.pack(output, self.root)
        self.assertEqual(result["sha256"], package.digest(output.read_bytes()))
        with tarfile.open(output) as archive:
            names = archive.getnames()
            self.assertIn(package.ARCHIVE_ROOT + "/" + package.THEME_DIRECTORY + "/apps/16@2x", names)
            self.assertFalse(any(".local" in name or ".git" in name or "packages/user-v1" in name for name in names))
            readme = archive.extractfile(package.ARCHIVE_ROOT + "/README.md").read()
            self.assertEqual(readme, (self.root / "ICONSET.md").read_bytes())
            self.assertTrue(all(member.isfile() or member.isdir() for member in archive))
        self.assertEqual(package.verify_archive(output)["sha256"], result["sha256"])
        self.assertEqual(output.with_name(output.name + ".sha256").read_text(), result["sha256"] + "  one.tar.gz\n")

    def test_file_only_checkout_collects_after_unused_native_directories_are_pruned(self):
        index = self.theme / "index.theme"
        index.write_text(prune_empty_icon_directories(index.read_text(), self.theme))
        # This test deliberately approves a pruned miniature release. Other
        # index mutations must continue to use the original fixture pin.
        package.V1_RELEASE_METADATA_DIGESTS[package.THEME_DIRECTORY + "/index.theme"] = package.digest(index.read_bytes())
        clone = self.root / "fresh-checkout"
        # Git preserves files and their parents, but drops empty directories.
        for path in tuple(self.root.rglob("*")):
            if path.is_file():
                target = clone / path.relative_to(self.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        self.assertFalse((clone / package.THEME_DIRECTORY / "apps/16@2x").exists())
        entries, directories = package.collect(clone)
        self.assertIn(package.THEME_DIRECTORY + "/apps/48/example.svg", entries)
        self.assertNotIn(package.THEME_DIRECTORY + "/apps/16@2x", directories)
        archive = self.root / "fresh-checkout.tar.gz"
        result = package.pack(archive, clone)
        self.assertEqual(package.verify_archive(archive)["sha256"], result["sha256"])

    def test_archive_bytes_are_reproducible(self):
        first = package.pack(self.root / "one.tar.gz", self.root)
        second = package.pack(self.root / "two.tar.gz", self.root)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_private_png_and_source_links_are_refused(self):
        unknown = self.theme / "local/16/apps/chatgpt.png"
        unknown.write_bytes(b"private logo")
        with self.assertRaises(ValueError):
            package.collect(self.root)
        unknown.unlink()
        source = self.root / "assets/icon-set-v1.0/witnessops-ai-cli.png"
        source.unlink()
        source.symlink_to(self.root / "LICENSE")
        with self.assertRaises(ValueError):
            package.collect(self.root)

    def test_native_drift_and_unrecorded_svg_are_refused(self):
        source = self.theme / "apps/48/example.svg"
        source.write_bytes(self.svg + b"changed")
        with self.assertRaises(ValueError):
            package.collect(self.root)
        source.write_bytes(self.svg)
        (self.theme / "apps/48/private.svg").write_bytes(self.svg)
        with self.assertRaises(ValueError):
            package.collect(self.root)

    def test_verifier_refuses_self_manifested_legacy_asset(self):
        output = self.root / "bad.tar.gz"
        private = b"old theme"
        manifest = (package.digest(private) + "  packages/user-v1/private.svg\n").encode()
        with tarfile.open(output, "w:gz") as archive:
            for name, data in (("packages/user-v1/private.svg", private), ("SHA256SUMS", manifest)):
                info = tarfile.TarInfo(package.ARCHIVE_ROOT + "/" + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        with self.assertRaises(ValueError):
            package.verify_archive(output)

    def test_private_artwork_metadata_is_refused(self):
        for metadata in ({"source": "/home/operator/private.png"}, {"source": "exec-01234567-abcd.png"}):
            with self.subTest(metadata=metadata):
                self.artwork.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    package.collect(self.root)

    def test_missing_advertised_directory_and_checksum_symlink_are_refused(self):
        (self.theme / "apps/16@2x").rmdir()
        with self.assertRaises(ValueError):
            package.collect(self.root)
        (self.theme / "apps/16@2x").mkdir()
        output = self.root / "one.tar.gz"
        output.with_name(output.name + ".sha256").symlink_to(self.root / "LICENSE")
        with self.assertRaises(ValueError):
            package.pack(output, self.root)
        self.assertFalse(output.exists())

    def test_seven_selected_applications_have_authored_pngs_at_every_size(self):
        entries, _ = package.collect(self.root)
        artwork = json.loads(self.artwork.read_text())
        self.assertEqual(len(artwork["assets"]), 7)
        self.assertEqual(len(artwork["rendered"]), 84)
        for record in artwork["rendered"]:
            data = entries[package.THEME_DIRECTORY + "/" + record["path"]]
            self.assertEqual(package.digest(data), record["sha256"])
            size = int(record["path"].split("/")[1])
            self.assertEqual(package.png_dimensions(data, record["path"]), (size, size))
        self.assertIn("assets/icon-set-v1.0/witnessops-settings.png", entries)
        for filename in ("witnessops-blackbox-browser.png", "witnessops-credential-import.png", "witnessops-vscodium.png", "witnessops-screenshot.png"):
            self.assertIn("assets/icon-set-v1.0/" + filename, entries)

    def test_browser_cube_wizard_vscodium_and_spectacle_use_their_own_scoped_keys(self):
        metadata = json.loads(self.artwork.read_text())
        sources = {record["path"].split("/")[-1]: record["icon_keys"] for record in metadata["assets"]}
        self.assertEqual(sources["witnessops-blackbox.png"], ["witnessops-blackbox"])
        self.assertEqual(sources["witnessops-blackbox-browser.png"], ["blackbox-browser"])
        self.assertEqual(sources["witnessops-credential-import.png"], ["witnessops-credential-import"])
        self.assertEqual(sources["witnessops-vscodium.png"], ["com.vscodium.codium"])
        self.assertEqual(sources["witnessops-screenshot.png"], ["spectacle"])
        sources_by_path = {record["path"]: record for record in metadata["assets"]}
        sources_by_path["assets/icon-set-v1.0/witnessops-blackbox-browser.png"]["icon_keys"] = ["witnessops-blackbox"]
        self.artwork.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "source or aliases differ"):
            package.collect(self.root)

    def test_wizard_artwork_does_not_replace_the_global_password_icon(self):
        self.native_asset("apps/48/dialog-password.svg", self.svg)
        entries, _ = package.collect(self.root)
        self.assertEqual(entries[package.THEME_DIRECTORY + "/apps/48/dialog-password.svg"], self.svg)
        self.assertNotIn("dialog-password", package.AUTHORED_KEYS)
        (self.theme / "local/16/apps/dialog-password.png").write_bytes(png(16))
        with self.assertRaisesRegex(ValueError, "Unexpected public theme asset"):
            package.collect(self.root)

    def test_small_authored_svg_overrides_are_refused_even_when_native_bytes_are_recorded(self):
        data = b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><path fill="currentColor" d="M2 2L8 6L2 10Z"/></svg>'
        self.native_asset("apps/16/utilities-terminal.svg", data)
        entries, _ = package.collect(self.root)
        self.assertEqual(entries[package.THEME_DIRECTORY + "/apps/16/utilities-terminal.svg"], data)
        self.native_asset("local/16/apps/konsole.svg", data)
        with self.assertRaisesRegex(ValueError, "Unexpected public theme asset"):
            package.collect(self.root)

    def test_selected_source_and_rendered_hash_drift_are_refused(self):
        for path in (self.root / "assets/icon-set-v1.0/witnessops-settings.png",
                     self.theme / "local/24/apps/witnessops-ai-cli.png"):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaisesRegex(ValueError, "hash differs"):
                    package.collect(self.root)
                path.write_bytes(original)

    def test_artwork_identity_sources_and_aliases_must_be_complete_and_exact(self):
        original = json.loads(self.artwork.read_text())
        mutations = (
            lambda value: value.update(version="2.0.0"),
            lambda value: value["assets"].pop(),
            lambda value: value["assets"].append(value["assets"][0]),
            lambda value: value["assets"][2]["icon_keys"].pop(),
            lambda value: value["assets"][2]["icon_keys"].append("chatgpt"),
            lambda value: value["assets"][2]["icon_keys"].append("preferences-system"),
        )
        for mutate in mutations:
            value = json.loads(json.dumps(original))
            mutate(value)
            self.artwork.write_text(json.dumps(value))
            with self.subTest(metadata=value["assets"]):
                with self.assertRaises(ValueError):
                    package.collect(self.root)

    def test_rendered_records_must_cover_all_sizes_without_duplicates_or_extra_paths(self):
        original = json.loads(self.artwork.read_text())
        mutations = (
            lambda value: value["rendered"].pop(0),
            lambda value: value["rendered"].append(value["rendered"][0]),
            lambda value: value["rendered"][0].update(path="local/22/apps/utilities-terminal.png"),
        )
        for mutate in mutations:
            value = json.loads(json.dumps(original))
            mutate(value)
            self.artwork.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                package.collect(self.root)
        self.artwork.write_text(json.dumps(original))
        (self.theme / "local/32/apps/org.kde.systemsettings.png").unlink()
        with self.assertRaisesRegex(ValueError, "Rendered artwork hash differs"):
            package.collect(self.root)

    def test_recorded_wrong_png_dimensions_and_differing_alias_bytes_are_refused(self):
        relative = "local/24/apps/konsole.png"
        target = self.theme / relative
        original_metadata = self.artwork.read_text()
        for data, message in ((png(32), "dimensions differ"),
                              (png(24, (12, 14, 16, 255)), "aliases differ")):
            target.write_bytes(data)
            metadata = json.loads(original_metadata)
            record = next(record for record in metadata["rendered"] if record["path"] == relative)
            record["sha256"] = package.digest(data)
            self.artwork.write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, message):
                package.collect(self.root)

    def test_recorded_invalid_source_png_header_is_refused(self):
        source = self.root / "assets/icon-set-v1.0/witnessops-settings.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nnot a png header")
        metadata = json.loads(self.artwork.read_text())
        next(record for record in metadata["assets"] if record["path"].endswith("settings.png"))["sha256"] = package.digest(source.read_bytes())
        self.artwork.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError, "valid PNG header"):
            package.collect(self.root)

    def test_native_folder_alias_metadata_matches_source_and_output_bytes(self):
        metadata, records = self.folder_aliases()
        entries, _ = package.collect(self.root)
        self.assertIn(metadata.relative_to(self.root).as_posix(), entries)
        for record in records:
            self.assertEqual(entries[package.THEME_DIRECTORY + "/" + record["path"]],
                             entries[package.THEME_DIRECTORY + "/" + record["source"]])
        records[0]["source_sha256"] = "0" * 64
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": records}))
        with self.assertRaises(ValueError):
            package.collect(self.root)

    def test_native_folder_alias_drift_missing_metadata_and_duplicate_are_refused(self):
        metadata, records = self.folder_aliases()
        target = self.theme / records[0]["path"]
        original = target.read_bytes()
        target.write_bytes(original + b"changed")
        with self.assertRaises(ValueError):
            package.collect(self.root)
        target.write_bytes(original)
        metadata.unlink()
        with self.assertRaises(ValueError):
            package.collect(self.root)
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": records + [records[0]]}))
        with self.assertRaises(ValueError):
            package.collect(self.root)

    def write_self_manifested_archive(self, entries, directories=(), modes=None):
        entries = {name: data for name, data in entries.items() if name != "SHA256SUMS"}
        manifest = "".join(f"{package.digest(data)}  {name}\n" for name, data in sorted(entries.items())).encode()
        output = self.root / "self-manifested.tar.gz"
        modes = modes or {}
        with tarfile.open(output, "w:gz") as archive:
            for name in ("", *sorted(directories)):
                info = tarfile.TarInfo(package.ARCHIVE_ROOT + ("/" + name if name else ""))
                info.type = tarfile.DIRTYPE
                info.mode = modes.get(name, 0o755)
                archive.addfile(info)
            for name, data in {**entries, "SHA256SUMS": manifest}.items():
                info = tarfile.TarInfo(package.ARCHIVE_ROOT + "/" + name)
                info.mode = modes.get(name, 0o755 if name in package.EXECUTABLES else 0o644)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output

    def test_verifier_accepts_canonical_archive_permission_modes(self):
        entries, directories = package.collect(self.root)
        archive = self.write_self_manifested_archive(entries, directories)
        self.assertEqual(package.verify_archive(archive)["files"], len(entries))
        with tarfile.open(archive) as reader:
            for member in reader:
                relative = member.name.removeprefix(package.ARCHIVE_ROOT + "/")
                expected = 0o755 if member.isdir() or relative in package.EXECUTABLES else 0o644
                self.assertEqual(member.mode, expected, member.name)

    def test_verifier_rejects_noncanonical_directory_permission_modes(self):
        entries, directories = package.collect(self.root)
        for name in ("", "assets", package.THEME_DIRECTORY + "/apps/16@2x"):
            for mode in (0o0000, 0o0644, 0o0700, 0o0777, 0o1755, 0o2755, 0o4755):
                with self.subTest(directory=name, mode=oct(mode)):
                    archive = self.write_self_manifested_archive(entries, directories, {name: mode})
                    with self.assertRaisesRegex(ValueError, "Noncanonical archive permission mode"):
                        package.verify_archive(archive)

    def test_verifier_rejects_noncanonical_executable_permission_modes(self):
        entries, directories = package.collect(self.root)
        for name in sorted(package.EXECUTABLES):
            for mode in (0o0000, 0o0644, 0o0744, 0o0777, 0o1755, 0o2755, 0o4755):
                with self.subTest(executable=name, mode=oct(mode)):
                    archive = self.write_self_manifested_archive(entries, directories, {name: mode})
                    with self.assertRaisesRegex(ValueError, "Noncanonical archive permission mode"):
                        package.verify_archive(archive)

    def test_verifier_rejects_noncanonical_asset_permission_modes(self):
        entries, directories = package.collect(self.root)
        for name in ("README.md", "SHA256SUMS", "LICENSE", "icon_set_style.py",
                     "tests/test_icon_set_package.py", package.THEME_DIRECTORY + "/apps/48/example.svg",
                     "assets/icon-set-v1.0/witnessops-ai-cli.png"):
            for mode in (0o0000, 0o0600, 0o0666, 0o0755, 0o1644, 0o2644, 0o4644):
                with self.subTest(asset=name, mode=oct(mode)):
                    archive = self.write_self_manifested_archive(entries, directories, {name: mode})
                    with self.assertRaisesRegex(ValueError, "Noncanonical archive permission mode"):
                        package.verify_archive(archive)

    def test_notice_file_is_canonical_and_required(self):
        notice = self.root / "THIRD_PARTY_NOTICES.md"
        notice.write_bytes(b"Current source attribution and license notices\n")
        # Deliberately approve this notice fixture before testing canonical copying.
        package.V1_RELEASE_METADATA_DIGESTS["THIRD_PARTY_NOTICES.md"] = package.digest(notice.read_bytes())
        result = package.pack(self.root / "notices.tar.gz", self.root)
        with tarfile.open(result["archive"]) as archive:
            data = archive.extractfile(package.ARCHIVE_ROOT + "/THIRD_PARTY_NOTICES.md").read()
        self.assertEqual(data, notice.read_bytes())
        notice.unlink()
        with self.assertRaisesRegex(ValueError, "Missing required regular file"):
            package.collect(self.root)

    def test_recorded_svgs_outside_native_publication_subtrees_are_refused(self):
        for relative in ("private.svg", "private/48/logo.svg", "apps/48/nested/logo.svg", "apps/999/logo.svg"):
            with self.subTest(relative=relative):
                self.native_asset(relative, self.svg)
                with self.assertRaisesRegex(ValueError, "Unexpected public theme asset"):
                    package.collect(self.root)
                (self.theme / relative).unlink()
                provenance_path = self.root / "assets/icon-set-v1.0/PROVENANCE.json"
                metadata = json.loads(provenance_path.read_text())
                metadata["records"].pop()
                metadata["file_count"] = len(metadata["records"])
                provenance_path.write_text(json.dumps(metadata))

    def test_verifier_rejects_self_manifested_readme_only_archive(self):
        archive = self.write_self_manifested_archive({"README.md": b"truncated archive\n"})
        with self.assertRaisesRegex(ValueError, "Missing mandatory public payload"):
            package.verify_archive(archive)

    def test_verifier_requires_scripts_licenses_and_recorded_theme_artwork(self):
        entries, directories = package.collect(self.root)
        for missing in ("README.md", "install_icon_set.py", "licenses/GPL-3.0.txt",
                        package.THEME_DIRECTORY + "/COPYRIGHT-BREEZE",
                        package.THEME_DIRECTORY + "/apps/48/example.svg"):
            with self.subTest(missing=missing):
                truncated = {name: data for name, data in entries.items() if name != missing}
                archive = self.write_self_manifested_archive(truncated, directories)
                with self.assertRaises(ValueError):
                    package.verify_archive(archive)

    def test_verifier_checks_provenance_even_when_internal_manifest_agrees(self):
        entries, directories = package.collect(self.root)
        native = package.THEME_DIRECTORY + "/apps/48/example.svg"
        entries[native] += b"changed artwork"
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Native provenance does not match"):
            package.verify_archive(archive)

    def test_verifier_checks_authored_png_dimensions_and_alias_hashes(self):
        entries, directories = package.collect(self.root)
        relative = "local/24/apps/konsole.png"
        native = package.THEME_DIRECTORY + "/" + relative
        metadata_name = "assets/icon-set-v1.0/artwork.json"
        original_metadata = entries[metadata_name]
        for data, message in ((png(32), "dimensions differ"),
                              (png(24, (3, 4, 5, 255)), "aliases differ")):
            with self.subTest(message=message):
                entries[native] = data
                metadata = json.loads(original_metadata)
                next(record for record in metadata["rendered"] if record["path"] == relative)["sha256"] = package.digest(data)
                entries[metadata_name] = json.dumps(metadata).encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, message):
                    package.verify_archive(archive)

    def test_verifier_checks_folder_aliases_from_streamed_integrity_records(self):
        self.folder_aliases()
        entries, directories = package.collect(self.root)
        valid = self.write_self_manifested_archive(entries, directories)
        self.assertEqual(package.verify_archive(valid)["files"], len(entries))
        alias = package.THEME_DIRECTORY + "/places/64/folder-work.svg"
        entries[alias] += b"changed alias"
        invalid = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Native folder alias differs"):
            package.verify_archive(invalid)

    def test_verifier_rejects_native_provenance_smuggling_without_folder_alias_manifest(self):
        self.folder_aliases()
        original, directories = package.collect(self.root)
        aliases_name = "assets/icon-set-v1.0/folder-aliases.json"
        provenance_name = "assets/icon-set-v1.0/PROVENANCE.json"
        records = json.loads(original[aliases_name])["records"]
        for alias in ("folder-work", "folder-projects"):
            with self.subTest(alias=alias):
                entries = dict(original)
                entries.pop(aliases_name)
                relative = f"places/64/{alias}.svg"
                entries[package.THEME_DIRECTORY + "/" + relative] = self.svg + b"arbitrary alias artwork"
                provenance = json.loads(entries[provenance_name])
                # Reclassify every alias as native so none remains unrecorded.
                for record in records:
                    provenance["records"].append({"path": record["path"],
                        "sha256": package.digest(entries[package.THEME_DIRECTORY + "/" + record["path"]])})
                provenance["file_count"] = len(provenance["records"])
                entries[provenance_name] = json.dumps(provenance).encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Missing mandatory public payload"):
                    package.verify_archive(archive)

    def test_verifier_rejects_reserved_alias_native_provenance_with_manifest_present(self):
        self.folder_aliases()
        original, directories = package.collect(self.root)
        aliases_name = "assets/icon-set-v1.0/folder-aliases.json"
        provenance_name = "assets/icon-set-v1.0/PROVENANCE.json"
        for alias in ("folder-work", "folder-projects"):
            with self.subTest(alias=alias):
                entries = dict(original)
                relative = f"places/64/{alias}.svg"
                entries[package.THEME_DIRECTORY + "/" + relative] = self.svg + b"arbitrary alias artwork"
                provenance = json.loads(entries[provenance_name])
                provenance["records"].append({"path": relative,
                    "sha256": package.digest(entries[package.THEME_DIRECTORY + "/" + relative])})
                provenance["file_count"] = len(provenance["records"])
                entries[provenance_name] = json.dumps(provenance).encode()
                aliases = json.loads(entries[aliases_name])
                aliases["records"] = [record for record in aliases["records"] if record["path"] != relative]
                entries[aliases_name] = json.dumps(aliases).encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 native inventory differs"):
                    package.verify_archive(archive)

    def test_published_alias_assets_require_the_folder_alias_manifest(self):
        metadata, _ = self.folder_aliases()
        entries, directories = package.collect(self.root)
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "Missing required regular file"):
            package.collect(self.root)
        entries.pop(metadata.relative_to(self.root).as_posix())
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Missing mandatory public payload"):
            package.verify_archive(archive)

    def test_reserved_alias_names_cannot_be_native_records_in_other_categories(self):
        metadata = self.root / "assets/icon-set-v1.0/folder-aliases.json"
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": []}))
        provenance_path = self.root / "assets/icon-set-v1.0/PROVENANCE.json"
        for category in ("apps", "actions"):
            for alias in ("folder-work", "folder-projects"):
                with self.subTest(category=category, alias=alias):
                    relative = f"{category}/48/{alias}.svg"
                    self.native_asset(relative, self.svg)
                    with self.assertRaisesRegex(ValueError, "Reserved folder alias"):
                        package.collect(self.root)
                    (self.theme / relative).unlink()
                    provenance = json.loads(provenance_path.read_text())
                    provenance["records"].pop()
                    provenance["file_count"] = len(provenance["records"])
                    provenance_path.write_text(json.dumps(provenance))

    def test_folder_alias_source_and_target_size_must_match(self):
        metadata, records = self.folder_aliases()
        data = (self.theme / records[0]["source"]).read_bytes()
        source = "places/32/folder-documents.svg"
        self.native_asset(source, data)
        records[0]["source"] = source
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": records}))
        with self.assertRaisesRegex(ValueError, "Folder alias is outside"):
            package.collect(self.root)

    def test_unknown_folder_alias_cannot_claim_none_as_its_canonical_source(self):
        metadata, records = self.folder_aliases()
        data = (self.theme / records[0]["path"]).read_bytes()
        (self.theme / records[0]["path"]).unlink()
        target = "places/64/folder-unknown.svg"
        source = "places/64/None.svg"
        self.native_asset(source, data)
        (self.theme / target).write_bytes(data)
        records[0].update(path=target, source=source)
        metadata.write_text(json.dumps({"schema_version": 1, "theme_identity": package.THEME, "records": records}))
        with self.assertRaisesRegex(ValueError, "Folder alias is outside"):
            package.collect(self.root)

    def test_frozen_release_pins_match_committed_native_manifests(self):
        for name, expected in self.release_pins.items():
            with self.subTest(name=name):
                self.assertEqual(package.digest((package.ROOT / name).read_bytes()), expected)

    def test_verifier_rejects_native_set_truncated_to_one_rewritten_record(self):
        self.folder_aliases()
        entries, directories = package.collect(self.root)
        provenance_name = "assets/icon-set-v1.0/PROVENANCE.json"
        provenance = json.loads(entries[provenance_name])
        retained = provenance["records"][0]
        for name in tuple(entries):
            if name.startswith(package.THEME_DIRECTORY + "/") and name.endswith(".svg"):
                if name != package.THEME_DIRECTORY + "/" + retained["path"]:
                    entries.pop(name)
        provenance.update(records=[retained], file_count=1)
        entries[provenance_name] = json.dumps(provenance).encode()
        # Both manifests and checksums agree with the reduced payload, but the trusted pins do not.
        aliases_name = "assets/icon-set-v1.0/folder-aliases.json"
        aliases = json.loads(entries[aliases_name])
        aliases["records"] = []
        entries[aliases_name] = json.dumps(aliases).encode()
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Frozen v1 native inventory differs"):
            package.verify_archive(archive)
        entries.pop(aliases_name)
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Missing mandatory public payload"):
            package.verify_archive(archive)

    def test_collect_rejects_self_consistent_truncated_native_inventory(self):
        provenance_path = self.root / "assets/icon-set-v1.0/PROVENANCE.json"
        provenance = json.loads(provenance_path.read_bytes())
        removed = provenance["records"].pop()
        (self.theme / removed["path"]).unlink()
        provenance["file_count"] = len(provenance["records"])
        provenance_path.write_text(json.dumps(provenance))
        with self.assertRaisesRegex(ValueError, "Frozen v1 native inventory differs"):
            package.collect(self.root)

    def test_verifier_rejects_same_count_native_path_and_hash_rewrites(self):
        original, directories = package.collect(self.root)
        provenance_name = "assets/icon-set-v1.0/PROVENANCE.json"
        for replace_path in (False, True):
            with self.subTest(replace_path=replace_path):
                entries = dict(original)
                provenance = json.loads(entries[provenance_name])
                record = provenance["records"][0]
                old_name = package.THEME_DIRECTORY + "/" + record["path"]
                changed = entries.pop(old_name) + b"replacement icon"
                if replace_path:
                    record["path"] = "apps/48/replacement.svg"
                entries[package.THEME_DIRECTORY + "/" + record["path"]] = changed
                record["sha256"] = package.digest(changed)
                entries[provenance_name] = json.dumps(provenance).encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 native inventory differs"):
                    package.verify_archive(archive)

    def test_verifier_rejects_rewritten_alias_manifest_after_alias_omission(self):
        self.folder_aliases()
        original, directories = package.collect(self.root)
        aliases_name = "assets/icon-set-v1.0/folder-aliases.json"
        for remove_all in (False, True):
            with self.subTest(remove_all=remove_all):
                entries = dict(original)
                aliases = json.loads(entries[aliases_name])
                removed = list(aliases["records"]) if remove_all else [aliases["records"][0]]
                for record in removed:
                    entries.pop(package.THEME_DIRECTORY + "/" + record["path"])
                    aliases["records"].remove(record)
                entries[aliases_name] = json.dumps(aliases).encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 native inventory differs"):
                    package.verify_archive(archive)

    def test_frozen_artwork_pin_matches_committed_inventory(self):
        self.assertEqual(package.digest((package.ROOT / "assets/icon-set-v1.0/artwork.json").read_bytes()),
                         self.release_artwork_pin)

    def substitute_artwork(self, original, replace_sources, replace_rendered):
        entries = dict(original)
        metadata_name = "assets/icon-set-v1.0/artwork.json"
        artwork = json.loads(entries[metadata_name])
        if replace_sources:
            for record in artwork["assets"]:
                entries[record["path"]] = png(256, (15, 16, 17, 255))
                record["sha256"] = package.digest(entries[record["path"]])
        if replace_rendered:
            for record in artwork["rendered"]:
                name = package.THEME_DIRECTORY + "/" + record["path"]
                entries[name] = png(int(record["path"].split("/")[1]), (15, 16, 17, 255))
                record["sha256"] = package.digest(entries[name])
        entries[metadata_name] = json.dumps(artwork).encode()
        return entries

    def test_verifier_rejects_self_consistent_substituted_artwork(self):
        original, directories = package.collect(self.root)
        for replace_sources, replace_rendered in ((True, False), (False, True), (True, True)):
            with self.subTest(sources=replace_sources, rendered=replace_rendered):
                entries = self.substitute_artwork(original, replace_sources, replace_rendered)
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 artwork inventory differs"):
                    package.verify_archive(archive)

    def test_collect_rejects_self_consistent_substituted_artwork(self):
        original, _ = package.collect(self.root)
        entries = self.substitute_artwork(original, True, True)
        for name, data in entries.items():
            if name == "assets/icon-set-v1.0/artwork.json" or name.endswith(".png"):
                (self.root / name).write_bytes(data)
        with self.assertRaisesRegex(ValueError, "Frozen v1 artwork inventory differs"):
            package.collect(self.root)

    def test_frozen_code_pins_cover_all_published_python_and_match_committed_sources(self):
        declared = {name for name in package.FILE_ALLOWLIST + package.OPTIONAL_FILES
                    if name.endswith(".py")}
        self.assertEqual(declared, set(self.release_code_pins) | {"pack_icon_set.py"})
        for name, expected in self.release_code_pins.items():
            with self.subTest(name=name):
                self.assertEqual(package.digest((package.ROOT / name).read_bytes()), expected)
        self.assertEqual(package.digest(Path(package.__file__).read_bytes()),
                         package.V1_PACKER_DIGEST)

    def test_verifier_rejects_every_self_manifested_public_python_substitution(self):
        original, directories = package.collect(self.root)
        published_python = sorted(name for name in original if name.endswith(".py"))
        # Include non-executable imported modules and optional tests/tools, not only
        # the entry points currently listed in EXECUTABLES.
        self.assertIn("install_icon_set.py", published_python)
        self.assertIn("tools/launcher_icon_overrides.py", published_python)
        self.assertIn("icon_set_style.py", published_python)
        self.assertIn("tests/test_icon_set_package.py", published_python)
        self.assertIn("tools/preview_icon_set.py", published_python)
        for name in published_python:
            with self.subTest(name=name):
                entries = dict(original)
                entries[name] += b"\n# attacker replacement with regenerated checksums\n"
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 code inventory differs: " + name):
                    package.verify_archive(archive)

    def test_collect_rejects_every_public_python_substitution(self):
        original, _ = package.collect(self.root)
        for name in sorted(name for name in original if name.endswith(".py")):
            with self.subTest(name=name):
                source = self.root / name
                source.write_bytes(original[name] + b"\n# replaced source code\n")
                try:
                    with self.assertRaisesRegex(ValueError, "Frozen v1 code inventory differs: " + name):
                        package.collect(self.root)
                finally:
                    source.write_bytes(original[name])

    def test_packer_pin_does_not_follow_the_root_being_verified(self):
        entries, directories = package.collect(self.root)
        name = "pack_icon_set.py"
        entries[name] += b"\n# altered verifier and trust constants\n"
        (self.root / name).write_bytes(entries[name])
        archive = self.write_self_manifested_archive(entries, directories)
        with patch.object(package, "ROOT", self.root):
            with self.assertRaisesRegex(ValueError, "Frozen v1 code inventory differs: " + name):
                package.verify_archive(archive)
            with self.assertRaisesRegex(ValueError, "Frozen v1 code inventory differs: " + name):
                package.collect(self.root)

    def test_new_public_python_requires_an_explicit_trusted_pin(self):
        entries, directories = package.collect(self.root)
        name = "tools/unpinned.py"
        entries[name] = b"# newly allowlisted Python without a trusted pin\n"
        (self.root / name).write_bytes(entries[name])
        archive = self.write_self_manifested_archive(entries, directories)
        with patch.object(package, "OPTIONAL_FILES", package.OPTIONAL_FILES + (name,)):
            with self.assertRaisesRegex(ValueError, "Unpinned public Python inventory"):
                package.verify_archive(archive)
            with self.assertRaisesRegex(ValueError, "Unpinned public Python inventory"):
                package.collect(self.root)

    def test_optional_pinned_python_may_be_omitted(self):
        for name in package.OPTIONAL_FILES:
            if name.endswith(".py"):
                (self.root / name).unlink()
        entries, directories = package.collect(self.root)
        self.assertFalse(any(name in entries for name in package.OPTIONAL_FILES if name.endswith(".py")))
        archive = self.write_self_manifested_archive(entries, directories)
        self.assertEqual(package.verify_archive(archive)["files"], len(entries))

    def test_frozen_metadata_pins_cover_guide_and_index_and_match_committed_sources(self):
        self.assertEqual(set(self.release_metadata_pins), {
            "ICONSET.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
            "licenses/Breeze-COPYING-ICONS", "licenses/GPL-3.0.txt",
            "licenses/LGPL-2.1.txt", "licenses/CC-BY-SA-4.0.txt",
            package.THEME_DIRECTORY + "/index.theme",
            package.THEME_DIRECTORY + "/COPYRIGHT-BREEZE",
            package.THEME_DIRECTORY + "/COPYING-BREEZE-ICONS",
        })
        for name, expected in self.release_metadata_pins.items():
            with self.subTest(name=name):
                self.assertEqual(package.digest((package.ROOT / name).read_bytes()), expected)

    def test_verifier_rejects_matching_readme_and_guide_replacement_with_rewritten_checksums(self):
        entries, directories = package.collect(self.root)
        replaced = b"# Installation\nRun the substituted download command before installing.\n"
        entries["README.md"] = entries["ICONSET.md"] = replaced
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: ICONSET.md"):
            package.verify_archive(archive)

    def test_collect_rejects_replaced_installation_guide(self):
        (self.root / "ICONSET.md").write_bytes(b"# Installation\nRun substituted commands.\n")
        with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: ICONSET.md"):
            package.collect(self.root)

    def test_verifier_rejects_every_self_manifested_release_metadata_substitution(self):
        original, directories = package.collect(self.root)
        for name in self.release_metadata_pins:
            with self.subTest(name=name):
                entries = dict(original)
                entries[name] += b"\nSubstituted release instructions or metadata.\n"
                if name == "ICONSET.md":
                    entries["README.md"] = entries[name]
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: " + name):
                    package.verify_archive(archive)

    def test_collect_rejects_every_release_metadata_source_substitution(self):
        original, _ = package.collect(self.root)
        for name in self.release_metadata_pins:
            with self.subTest(name=name):
                source = self.root / name
                source.write_bytes(original[name] + b"\nSubstituted release metadata.\n")
                try:
                    with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: " + name):
                        package.collect(self.root)
                finally:
                    source.write_bytes(original[name])

    def test_new_public_metadata_requires_an_explicit_trusted_pin(self):
        entries, directories = package.collect(self.root)
        name = "docs/unpinned.md"
        entries[name] = b"# Newly allowlisted instructions without a trusted pin\n"
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entries[name])
        archive = self.write_self_manifested_archive(entries, directories)
        with patch.object(package, "OPTIONAL_FILES", package.OPTIONAL_FILES + (name,)):
            with self.assertRaisesRegex(ValueError, "Unpinned public release metadata"):
                package.verify_archive(archive)
            with self.assertRaisesRegex(ValueError, "Unpinned public release metadata"):
                package.collect(self.root)

    def test_unused_source_metadata_is_excluded_and_refused_in_repacked_archive(self):
        name = "assets/icon-set-v1.0/SOURCE.json"
        (self.root / name).write_bytes(b'{"instructions": "substituted installation command"}')
        entries, directories = package.collect(self.root)
        self.assertNotIn(name, entries)
        entries[name] = (self.root / name).read_bytes()
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "outside the publication allowlist"):
            package.verify_archive(archive)

    def mutated_theme_indexes(self, original):
        lines = original.splitlines(keepends=True)
        without_directories = "".join(line for line in lines if not line.startswith("Directories="))
        without_scaled = "".join(line for line in lines if not line.startswith("ScaledDirectories="))
        missing = "".join(line for line in lines
                          if not line.startswith(("Directories=", "ScaledDirectories=")))
        empty = "".join(line.split("=", 1)[0] + "=\n"
                        if line.startswith(("Directories=", "ScaledDirectories=")) else line
                        for line in lines)
        partial = "".join("Directories=apps/48\n" if line.startswith("Directories=")
                          else "ScaledDirectories=\n" if line.startswith("ScaledDirectories=")
                          else line for line in lines)
        return (
            ("empty directory lists", empty),
            ("missing directory lists", missing),
            ("missing Directories", without_directories),
            ("missing ScaledDirectories", without_scaled),
            ("partial directory lists", partial),
            ("altered Size", original.replace("[apps/48]\nSize=48\n", "[apps/48]\nSize=1\n")),
            ("altered Context", original.replace("[apps/48]\n", "[apps/48]\nContext=Actions\n")),
            ("missing advertised section", original.replace("[apps/48]\nSize=48\n", "")),
        )

    def test_collect_rejects_index_directory_lists_and_section_mutations(self):
        index = self.theme / "index.theme"
        original = index.read_text()
        for label, changed in self.mutated_theme_indexes(original):
            with self.subTest(mutation=label):
                self.assertNotEqual(changed, original)
                index.write_text(changed)
                try:
                    with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: "
                                                + package.THEME_DIRECTORY + "/index.theme"):
                        package.collect(self.root)
                finally:
                    index.write_text(original)

    def test_verifier_rejects_index_mutations_with_rewritten_checksums(self):
        original, directories = package.collect(self.root)
        name = package.THEME_DIRECTORY + "/index.theme"
        for label, changed in self.mutated_theme_indexes(original[name].decode()):
            with self.subTest(mutation=label):
                entries = dict(original)
                entries[name] = changed.encode()
                archive = self.write_self_manifested_archive(entries, directories)
                with self.assertRaisesRegex(ValueError, "Frozen v1 release metadata differs: " + name):
                    package.verify_archive(archive)

    def test_optional_preview_tool_is_allowlisted(self):
        source = self.root / "tools/preview_icon_set.py"
        entries, _ = package.collect(self.root)
        self.assertEqual(entries["tools/preview_icon_set.py"], source.read_bytes())


class IconSetArchiveLimitsTest(unittest.TestCase):
    """Adversarial tar headers stay small; no oversized fixture bodies exist."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def raw_header(self, relative="LICENSE", size=0, kind=tarfile.REGTYPE, mode=None):
        info = tarfile.TarInfo(package.ARCHIVE_ROOT + "/" + relative)
        info.size = size
        info.type = kind
        info.mode = (0o755 if kind == tarfile.DIRTYPE or relative in package.EXECUTABLES
                     else 0o644) if mode is None else mode
        info.linkname = "unused-target" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
        header = bytearray(info.tobuf(format=tarfile.USTAR_FORMAT))
        if mode is not None:
            # TarInfo.tobuf masks upper bits; write the adversarial raw field.
            header[100:108] = f"{mode:07o}\0".encode()
            header[148:156] = b"        "
            header[148:156] = f"{sum(header):06o}\0 ".encode()
        return bytes(header)

    def padded(self, data):
        return data + b"\0" * (-len(data) % tarfile.BLOCKSIZE)

    def pax(self, fields, declared_size=None):
        records = []
        for key, value in fields.items():
            suffix = f" {key}={value}\n".encode()
            length = len(suffix) + 1
            while length != len(str(length)) + len(suffix):
                length = len(str(length)) + len(suffix)
            records.append(str(length).encode() + suffix)
        body = b"".join(records)
        size = len(body) if declared_size is None else declared_size
        return self.raw_header("pax", size, tarfile.XHDTYPE) + self.padded(body)

    def archive(self, raw):
        path = self.root / "adversarial.tar.gz"
        path.write_bytes(gzip.compress(raw + b"\0" * 1024, mtime=0))
        return path

    def assert_before_extract(self, archive, reason):
        with patch.object(tarfile.TarFile, "extractfile", side_effect=AssertionError("body extraction attempted")):
            with self.assertRaisesRegex(ValueError, reason):
                package.verify_archive(archive)

    def test_permission_modes_are_refused_before_extracting_body(self):
        cases = (("", tarfile.DIRTYPE, 0o0000),
                 ("assets", tarfile.DIRTYPE, 0o0777),
                 ("install_icon_set.py", tarfile.REGTYPE, 0o0644),
                 ("LICENSE", tarfile.REGTYPE, 0o0755),
                 ("SHA256SUMS", tarfile.REGTYPE, 0o4644),
                 ("", tarfile.DIRTYPE, 0o10755),
                 ("install_icon_set.py", tarfile.REGTYPE, 0o10755),
                 ("LICENSE", tarfile.REGTYPE, 0o10644))
        for name, kind, mode in cases:
            with self.subTest(member=name, mode=oct(mode)):
                archive = self.archive(self.raw_header(name, kind=kind, mode=mode))
                self.assert_before_extract(archive, "Noncanonical archive permission mode")
        # Apply the policy to the final PAX-resolved publication path.
        long_path = package.ARCHIVE_ROOT + "/" + package.THEME_DIRECTORY + "/apps/48/" + "a" * 120 + ".svg"
        archive = self.archive(self.pax({"path": long_path})
                               + self.raw_header("install_icon_set.py", mode=0o0755))
        self.assert_before_extract(archive, "Noncanonical archive permission mode")

    def test_oversized_regular_header_is_refused_before_extracting_body(self):
        archive = self.archive(self.raw_header(size=package.MAX_MEMBER_BYTES + 1))
        self.assert_before_extract(archive, "member size budget exceeded")

    def test_each_metadata_header_obeys_its_limit_before_extracting_body(self):
        for relative, limit in package.METADATA_LIMITS.items():
            with self.subTest(relative=relative):
                archive = self.archive(self.raw_header(relative, limit + 1))
                self.assert_before_extract(archive, "member size budget exceeded")

    def test_oversized_pax_header_is_refused_before_parser_reads_extension_body(self):
        archive = self.archive(self.raw_header("pax", package.MAX_PAX_HEADER_BYTES + 1, tarfile.XHDTYPE))
        with patch.object(tarfile.TarInfo, "_proc_pax", side_effect=AssertionError("PAX body parser attempted")):
            self.assert_before_extract(archive, "PAX header size budget exceeded")

    def test_chained_path_pax_headers_are_refused_before_body_extraction(self):
        path = package.ARCHIVE_ROOT + "/LICENSE"
        archive = self.archive(self.pax({"path": path}) + self.pax({"path": path}) + self.raw_header())
        self.assert_before_extract(archive, "Chained archive PAX headers")

    def test_all_pax_sparse_variants_are_refused_before_sparse_body_parser(self):
        variants = (
            ("_proc_gnusparse_00", {"GNU.sparse.size": "999999999", "GNU.sparse.offset": "0", "GNU.sparse.numbytes": "1"}),
            ("_proc_gnusparse_01", {"GNU.sparse.map": "0,999999999"}),
            ("_proc_gnusparse_10", {"GNU.sparse.major": "1", "GNU.sparse.minor": "0"}),
        )
        for handler, fields in variants:
            with self.subTest(handler=handler):
                archive = self.archive(self.pax(fields) + self.raw_header(size=512))
                with patch.object(tarfile.TarInfo, handler, side_effect=AssertionError("sparse map parser attempted")):
                    self.assert_before_extract(archive, "Sparse archive extensions")

    def test_non_path_pax_fields_cannot_change_member_semantics(self):
        for fields in ({"size": "1"}, {"linkpath": "outside"}, {"comment": "unused"}):
            with self.subTest(fields=fields):
                archive = self.archive(self.pax(fields) + self.raw_header())
                self.assert_before_extract(archive, "PAX fields other than path")

    def test_unsupported_tar_types_and_directory_payload_are_refused_before_body(self):
        for kind in (tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                     tarfile.GNUTYPE_SPARSE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                archive = self.archive(self.raw_header(kind=kind))
                self.assert_before_extract(archive, "unsupported header type")
        archive = self.archive(self.raw_header(size=1, kind=tarfile.DIRTYPE))
        self.assert_before_extract(archive, "member size budget exceeded")

    def test_raw_header_budget_counts_local_pax_header_before_yielding_member(self):
        path = package.ARCHIVE_ROOT + "/LICENSE"
        archive = self.archive(self.pax({"path": path}) + self.raw_header())
        with patch.object(package, "MAX_ARCHIVE_HEADERS", 1):
            self.assert_before_extract(archive, "header count budget exceeded")

    def test_member_budget_accumulates_before_extraction_of_excess_member(self):
        archive = self.archive(self.raw_header() + self.raw_header("ICONSET.md"))
        original_extract = tarfile.TarFile.extractfile
        extracted = []

        def record_extract(reader, member):
            extracted.append(member.name)
            return original_extract(reader, member)

        with patch.object(package, "MAX_ARCHIVE_MEMBERS", 1), \
                patch.object(tarfile.TarFile, "extractfile", new=record_extract):
            with self.assertRaisesRegex(ValueError, "member count budget exceeded"):
                package.verify_archive(archive)
        self.assertEqual(extracted, [package.ARCHIVE_ROOT + "/LICENSE"])

    def test_payload_budget_is_enforced_from_declared_size_before_extraction(self):
        archive = self.archive(self.raw_header(size=17))
        with patch.object(package, "MAX_PAYLOAD_BYTES", 16):
            self.assert_before_extract(archive, "payload byte budget exceeded")

    def test_payload_budget_accumulates_across_members(self):
        archive = self.archive(self.raw_header(size=8) + self.padded(b"a" * 8)
                               + self.raw_header("ICONSET.md", 9))
        original_extract = tarfile.TarFile.extractfile
        extracted = []

        def record_extract(reader, member):
            extracted.append(member.name)
            return original_extract(reader, member)

        with patch.object(package, "MAX_PAYLOAD_BYTES", 16), \
                patch.object(tarfile.TarFile, "extractfile", new=record_extract):
            with self.assertRaisesRegex(ValueError, "payload byte budget exceeded"):
                package.verify_archive(archive)
        self.assertEqual(extracted, [package.ARCHIVE_ROOT + "/LICENSE"])

    def test_decompressed_budget_counts_actual_padding_and_trailing_data(self):
        # Tar processing stops at its zero trailer, but verification must still
        # bound and consume the rest of the gzip stream.
        archive = self.archive(self.raw_header() + b"\0" * (24 * 1024))
        with patch.object(package, "MAX_DECOMPRESSED_BYTES", 12 * 1024):
            with self.assertRaisesRegex(ValueError, "Archive byte budget exceeded"):
                package.verify_archive(archive)

    def test_compressed_budget_is_checked_before_opening_gzip_parser(self):
        archive = self.archive(self.raw_header())
        with patch.object(package, "MAX_COMPRESSED_BYTES", archive.stat().st_size - 1), \
                patch.object(gzip, "GzipFile", side_effect=AssertionError("gzip parser attempted")):
            self.assert_before_extract(archive, "Compressed archive size budget exceeded")

    def test_limited_reader_bounds_actual_source_requests_and_rejects_unbounded_reads(self):
        class Source(io.BytesIO):
            def read(self, size=-1):
                requests.append(size)
                return super().read(size)

        requests = []
        reader = package.LimitedReader(Source(b"x" * (2 * package.READ_CHUNK_BYTES)), package.READ_CHUNK_BYTES)
        with self.assertRaisesRegex(ValueError, "Unbounded archive reads"):
            reader.read()
        self.assertEqual(requests, [])
        self.assertEqual(len(reader.read(2 * package.READ_CHUNK_BYTES)), package.READ_CHUNK_BYTES)
        with self.assertRaisesRegex(ValueError, "Archive byte budget exceeded"):
            reader.read(package.READ_CHUNK_BYTES)
        self.assertEqual(requests, [package.READ_CHUNK_BYTES, 1])

    def valid_fixture_archive(self):
        fixture = IconSetPackageTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        archive = fixture.root / "valid-limits.tar.gz"
        package.pack(archive, fixture.root)
        return fixture, archive

    def test_nonzero_concatenated_gzip_payload_is_refused_but_zero_padding_is_allowed(self):
        _, archive = self.valid_fixture_archive()
        original = archive.read_bytes()
        archive.write_bytes(original + gzip.compress(b"\0" * 2048, mtime=0))
        result = package.verify_archive(archive)
        self.assertEqual(result["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
        hidden_tar = self.raw_header("private.txt", 6) + self.padded(b"hidden") + b"\0" * 1024
        archive.write_bytes(original + gzip.compress(hidden_tar, mtime=0))
        with self.assertRaises(ValueError):
            package.verify_archive(archive)

    def test_malformed_nonzero_header_before_tar_terminators_is_refused(self):
        _, archive = self.valid_fixture_archive()
        raw = gzip.decompress(archive.read_bytes())
        with tarfile.open(archive, "r:gz") as reader:
            end = max(member.offset_data + (member.size + 511) // 512 * 512
                      for member in reader)
        self.assertEqual(raw[end:end + 512], b"\0" * 512)
        invalid_header = b"malformed-nonzero-header".ljust(512, b"!")
        archive.write_bytes(gzip.compress(raw[:end] + invalid_header + raw[end:], mtime=0))
        with self.assertRaises(ValueError):
            package.verify_archive(archive)

    def test_valid_archive_hashes_members_and_compressed_input_in_bounded_chunks(self):
        fixture = IconSetPackageTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Deterministic, poorly compressible SVG text forces several source
        # and member reads without relying on a large or random fixture.
        content = b"".join(hashlib.sha256(str(number).encode()).hexdigest().encode()
                           for number in range(4096))
        fixture.native_asset("apps/48/large.svg", b"<svg><desc>" + content + b"</desc></svg>")
        archive = fixture.root / "bounded-valid.tar.gz"
        package.pack(archive, fixture.root)
        expected = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertGreater(archive.stat().st_size, package.READ_CHUNK_BYTES)
        member_requests, archive_requests = [], []
        original_member_read = tarfile.ExFileObject.read
        original_open = Path.open

        def bounded_member_read(stream, size=-1):
            self.assertGreaterEqual(size, 0)
            self.assertLessEqual(size, package.READ_CHUNK_BYTES)
            member_requests.append(size)
            return original_member_read(stream, size)

        class RawReadSpy:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def fileno(self):
                return self.stream.fileno()

            def read(self, size=-1):
                self_outer.assertGreaterEqual(size, 0)
                self_outer.assertLessEqual(size, package.READ_CHUNK_BYTES)
                archive_requests.append(size)
                return self.stream.read(size)

        self_outer = self

        def observed_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return RawReadSpy(stream) if path == archive else stream

        with patch.object(tarfile.ExFileObject, "read", new=bounded_member_read), \
                patch.object(Path, "open", new=observed_open), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("whole archive read attempted")):
            result = package.verify_archive(archive)
        self.assertEqual(result["sha256"], expected)
        self.assertIn(package.READ_CHUNK_BYTES, member_requests)
        self.assertGreater(len(archive_requests), 2)

if __name__ == "__main__":
    unittest.main()
