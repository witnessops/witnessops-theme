"""Standalone archive boundaries, integrity and repeatability checks."""

from pathlib import Path
import io
import json
import struct
import shutil
import tarfile
import tempfile
import unittest
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
        (self.theme / "apps/48/example.svg").write_bytes(self.svg)
        for name in ("COPYRIGHT-BREEZE", "COPYING-BREEZE-ICONS"):
            (self.theme / name).write_text("Breeze license fixture\n")
        provenance = {"theme_identity": package.THEME, "version": package.VERSION,
                      "file_count": 1, "records": [{"path": "apps/48/example.svg", "sha256": package.digest(self.svg)}]}
        (self.root / "assets/icon-set-v1.0/PROVENANCE.json").write_text(json.dumps(provenance))
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
        for name in (".local/private.svg", ".git/history", "packages/user-v1/old.svg", "README.md"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("must be excluded")

    def native_asset(self, relative, data):
        path = self.theme / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        provenance_path = self.root / "assets/icon-set-v1.0/PROVENANCE.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["records"].append({"path": relative, "sha256": package.digest(data)})
        provenance["file_count"] = len(provenance["records"])
        provenance_path.write_text(json.dumps(provenance))

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

    def write_self_manifested_archive(self, entries, directories=()):
        entries = {name: data for name, data in entries.items() if name != "SHA256SUMS"}
        manifest = "".join(f"{package.digest(data)}  {name}\n" for name, data in sorted(entries.items())).encode()
        output = self.root / "self-manifested.tar.gz"
        with tarfile.open(output, "w:gz") as archive:
            for name in ("", *sorted(directories)):
                info = tarfile.TarInfo(package.ARCHIVE_ROOT + ("/" + name if name else ""))
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            for name, data in {**entries, "SHA256SUMS": manifest}.items():
                info = tarfile.TarInfo(package.ARCHIVE_ROOT + "/" + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output

    def test_notice_file_is_canonical_and_required(self):
        notice = self.root / "THIRD_PARTY_NOTICES.md"
        notice.write_bytes(b"Current source attribution and license notices\n")
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
                with self.assertRaisesRegex(ValueError, "Missing native folder alias metadata"):
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
                with self.assertRaisesRegex(ValueError, "Reserved folder alias"):
                    package.verify_archive(archive)

    def test_published_alias_assets_require_the_folder_alias_manifest(self):
        metadata, _ = self.folder_aliases()
        entries, directories = package.collect(self.root)
        metadata.unlink()
        with self.assertRaisesRegex(ValueError, "Missing native folder alias metadata"):
            package.collect(self.root)
        entries.pop(metadata.relative_to(self.root).as_posix())
        archive = self.write_self_manifested_archive(entries, directories)
        with self.assertRaisesRegex(ValueError, "Missing native folder alias metadata"):
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

    def test_optional_preview_tool_is_allowlisted(self):
        source = self.root / "tools/preview_icon_set.py"
        source.write_text("#!/usr/bin/env python3\n# selected-asset diagnostics\n")
        entries, _ = package.collect(self.root)
        self.assertEqual(entries["tools/preview_icon_set.py"], source.read_bytes())


if __name__ == "__main__":
    unittest.main()
