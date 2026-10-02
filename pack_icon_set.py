#!/usr/bin/env python3
"""Create and verify an explicitly allowlisted standalone icon-set archive."""
from __future__ import annotations

import argparse
import configparser
from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parent
VERSION = "1.0.0"
THEME = "WitnessOpsIconsV1_0"
ARCHIVE_ROOT = "WitnessOps-Icon-Set-v" + VERSION
THEME_DIRECTORY = "packages/icon-set-v1.0/" + THEME
FILE_ALLOWLIST = (
    "ICONSET.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pack_icon_set.py", "build_icon_set.py",
    "icon_set_style.py", "install_icon_set.py", "tools/inventory_icons.py",
    "tools/launcher_icon_overrides.py", "tests/test_icon_inventory.py",
    "tests/test_icon_set_build.py", "tests/test_icon_set_style.py",
    "tests/test_icon_set_install.py", "tests/test_launcher_icon_overrides.py",
    "licenses/Breeze-COPYING-ICONS", "licenses/GPL-3.0.txt",
    "licenses/LGPL-2.1.txt", "licenses/CC-BY-SA-4.0.txt",
    "assets/icon-set-v1.0/PROVENANCE.json",
    "assets/icon-set-v1.0/artwork.json",
    "assets/icon-set-v1.0/witnessops-ai-cli.png",
    "assets/icon-set-v1.0/witnessops-blackbox.png",
    "assets/icon-set-v1.0/witnessops-settings.png",
    "assets/icon-set-v1.0/witnessops-blackbox-browser.png",
    "assets/icon-set-v1.0/witnessops-credential-import.png",
    "assets/icon-set-v1.0/witnessops-vscodium.png",
    "assets/icon-set-v1.0/witnessops-screenshot.png",
)
OPTIONAL_FILES = ("assets/icon-set-v1.0/SOURCE.json", "assets/icon-set-v1.0/folder-aliases.json",
                  "tests/test_icon_set_package.py", "tools/preview_icon_set.py")
EXECUTABLES = {"pack_icon_set.py", "build_icon_set.py", "install_icon_set.py",
               "tools/inventory_icons.py", "tools/launcher_icon_overrides.py", "tools/preview_icon_set.py"}
AUTHORED = {
    "witnessops-ai-cli.png": ("utilities-terminal", "konsole", "org.kde.konsole", "witnessops-ai-cli"),
    "witnessops-blackbox.png": ("witnessops-blackbox",),
    "witnessops-settings.png": ("preferences-system", "systemsettings", "org.kde.systemsettings"),
    "witnessops-blackbox-browser.png": ("blackbox-browser",),
    "witnessops-credential-import.png": ("witnessops-credential-import",),
    "witnessops-vscodium.png": ("com.vscodium.codium",),
    "witnessops-screenshot.png": ("spectacle",),
}
AUTHORED_KEYS = frozenset(key for keys in AUTHORED.values() for key in keys)
SIZES = (16, 24, 32, 48, 64, 128, 256)
FOLDER_ALIAS_CANONICAL = {"folder-work": "folder-documents", "folder-projects": "folder-development"}
NATIVE_CATEGORIES = frozenset((
    "actions", "animations", "applets", "apps", "categories", "devices",
    "emblems", "emotes", "mimetypes", "places", "preferences", "status",
))
NATIVE_DIRECTORIES = frozenset((
    "8", "12", "16", "22", "24", "32", "48", "64", "96", "128", "256",
    "16@2x", "16@3x", "22@2x", "22@3x", "24@2x", "24@3x", "32@2x", "32@3x",
))


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    """Retain integrity evidence without another copy of archive artwork bytes."""
    sha256: str
    header: bytes


def digest(data: bytes | VerifiedFile) -> str:
    return data.sha256 if isinstance(data, VerifiedFile) else hashlib.sha256(data).hexdigest()


def safe_relative(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or not path.parts
            or any(character in name for character in ("\n", "\r", "\x00", "\\"))):
        raise ValueError("Unsafe package member: " + repr(name))
    return path


def allowed_member(name: str) -> bool:
    if any(part.startswith(".") for part in PurePosixPath(name).parts):
        return False
    if name in FILE_ALLOWLIST + OPTIONAL_FILES + ("README.md", "SHA256SUMS"):
        return True
    prefix = THEME_DIRECTORY + "/"
    return name.startswith(prefix) and permitted_theme_file(name.removeprefix(prefix))


def allowed_directory(name: str) -> bool:
    if not name:
        return True
    path = PurePosixPath(name)
    if any(part.startswith(".") for part in path.parts):
        return False
    if name == THEME_DIRECTORY:
        return True
    prefix = THEME_DIRECTORY + "/"
    if name.startswith(prefix):
        parts = PurePosixPath(name.removeprefix(prefix)).parts
        if parts[0] in NATIVE_CATEGORIES:
            return len(parts) == 1 or (len(parts) == 2 and parts[1] in NATIVE_DIRECTORIES)
        return (parts == ("local",) or
                (len(parts) in (2, 3) and parts[0] == "local" and parts[1] in {str(size) for size in SIZES}
                 and (len(parts) == 2 or parts[2] == "apps")))
    return any(path in PurePosixPath(member).parents for member in FILE_ALLOWLIST + OPTIONAL_FILES + (THEME_DIRECTORY,))


def regular_file(root: Path, relative: str) -> bytes:
    path = root / safe_relative(relative)
    if any(component.is_symlink() for component in (path, *path.parents)):
        raise ValueError("Symbolic links are excluded: " + relative)
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Missing required regular file: " + relative)
    return path.read_bytes()


def permitted_theme_file(relative: str) -> bool:
    path = PurePosixPath(relative)
    if relative in {"index.theme", "COPYING-BREEZE-ICONS", "COPYRIGHT-BREEZE"}:
        return True
    if path.suffix == ".svg":
        return (len(path.parts) == 3 and path.parts[0] in NATIVE_CATEGORIES
                and path.parts[1] in NATIVE_DIRECTORIES)
    # Only the authored WitnessOps PNGs can appear in the public theme. Private
    # hicolor/host logo overlays must never enter through the directory allowlist.
    return (len(path.parts) == 4 and path.parts[0] == "local"
            and path.parts[1] in {str(size) for size in SIZES}
            and path.parts[2] == "apps" and path.suffix == ".png"
            and path.stem in AUTHORED_KEYS)


def collect(root: Path = ROOT) -> tuple[dict[str, bytes], set[str]]:
    """Snapshot only declared files, rejecting links and unexpected theme data."""
    root = Path(root).absolute()
    entries = {name: regular_file(root, name) for name in FILE_ALLOWLIST}
    for name in OPTIONAL_FILES:
        if (root / name).exists():
            entries[name] = regular_file(root, name)
    theme = root / THEME_DIRECTORY
    if not theme.is_dir() or theme.is_symlink():
        raise ValueError("Missing regular v1.0 theme directory")
    directories = {THEME_DIRECTORY}
    for path in sorted(theme.rglob("*")):
        relative = path.relative_to(root).as_posix()
        safe_relative(relative)
        if any(part.startswith(".") for part in PurePosixPath(relative).parts):
            raise ValueError("Hidden private state is excluded: " + relative)
        if path.is_symlink():
            raise ValueError("Symbolic links are excluded: " + relative)
        if path.is_dir():
            directories.add(relative)
        elif path.is_file() and permitted_theme_file(path.relative_to(theme).as_posix()):
            entries[relative] = regular_file(root, relative)
        else:
            raise ValueError("Unexpected public theme asset: " + relative)
    validate_payload(entries, directories)
    entries["README.md"] = entries["ICONSET.md"]
    entries["SHA256SUMS"] = "".join(f"{digest(data)}  {name}\n" for name, data in sorted(entries.items())).encode()
    for name in tuple(entries) + tuple(directories):
        directories.update(parent.as_posix() for parent in safe_relative(name).parents if parent.parts)
    return entries, directories


def validate_payload(entries: dict[str, bytes | VerifiedFile], directories: set[str]) -> None:
    required = set(FILE_ALLOWLIST) | {THEME_DIRECTORY + "/" + name for name in
                                     ("index.theme", "COPYRIGHT-BREEZE", "COPYING-BREEZE-ICONS")}
    missing = required - entries.keys()
    if missing:
        raise ValueError("Missing mandatory public payload: " + sorted(missing)[0])
    index_name = THEME_DIRECTORY + "/index.theme"
    if index_name not in entries:
        raise ValueError("Missing public theme index")
    index = configparser.ConfigParser(interpolation=None)
    index.optionxform = str
    index.read_string(entries[index_name].decode())
    if index["Icon Theme"]["Name"] != "WitnessOps Icons v1.0":
        raise ValueError("Theme index identity differs")
    if index["Icon Theme"].get("Inherits") != "breeze-dark,hicolor":
        raise ValueError("Original app fallback is missing")
    for field in ("Directories", "ScaledDirectories"):
        for directory in filter(None, index["Icon Theme"].get(field, "").split(",")):
            safe_relative(directory)
            if directory not in index or THEME_DIRECTORY + "/" + directory not in directories:
                raise ValueError("Unmaterialized advertised icon directory: " + directory)
    aliases_name = "assets/icon-set-v1.0/folder-aliases.json"
    alias_assets = {name for name in entries if name.startswith(THEME_DIRECTORY + "/")
                    and name.endswith(".svg") and PurePosixPath(name).stem in FOLDER_ALIAS_CANONICAL}
    if alias_assets and aliases_name not in entries:
        raise ValueError("Missing native folder alias metadata: " + aliases_name)
    provenance = json.loads(entries["assets/icon-set-v1.0/PROVENANCE.json"])
    if provenance.get("theme_identity") != THEME or provenance.get("version") != VERSION:
        raise ValueError("Native provenance identity differs")
    if (not isinstance(provenance.get("records"), list) or not provenance["records"]
            or len(provenance["records"]) != provenance.get("file_count")):
        raise ValueError("Incomplete native provenance records")
    seen = set()
    for record in provenance["records"]:
        relative = safe_relative(record["path"]).as_posix()
        name = THEME_DIRECTORY + "/" + relative
        if not relative.endswith(".svg") or not permitted_theme_file(relative):
            raise ValueError("Native provenance path is outside the declared publication scope: " + relative)
        if PurePosixPath(relative).stem in FOLDER_ALIAS_CANONICAL:
            raise ValueError("Reserved folder alias must use folder-aliases.json: " + relative)
        if name in seen or name not in entries or digest(entries[name]) != record["sha256"]:
            raise ValueError("Native provenance does not match public payload: " + relative)
        seen.add(name)
    if aliases_name in entries:
        aliases = json.loads(entries[aliases_name])
        if (not isinstance(aliases, dict) or aliases.get("schema_version") != 1
                or aliases.get("theme_identity") != THEME or not isinstance(aliases.get("records"), list)):
            raise ValueError("Invalid native folder alias metadata")
        native_seen = set(seen)
        for record in aliases["records"]:
            if not isinstance(record, dict) or not all(isinstance(record.get(key), str) for key in
                                                     ("path", "source", "source_sha256", "sha256")):
                raise ValueError("Incomplete native folder alias record")
            target = safe_relative(record["path"])
            source = safe_relative(record["source"])
            expected_source = FOLDER_ALIAS_CANONICAL.get(target.stem)
            if (len(target.parts) != 3 or target.parts[0] != "places" or target.suffix != ".svg"
                    or not re.fullmatch(r"\d+(?:@\d+x)?", target.parts[1])
                    or len(source.parts) != 3 or source.parts[0] != "places"
                    or expected_source is None or source.parts[1] != target.parts[1]
                    or source.name != expected_source + ".svg"):
                raise ValueError("Folder alias is outside its declared native scope: " + record["path"])
            target_name = THEME_DIRECTORY + "/" + target.as_posix()
            source_name = THEME_DIRECTORY + "/" + source.as_posix()
            if (target_name in seen or target_name not in entries or source_name not in native_seen
                    or digest(entries[source_name]) != record["source_sha256"]
                    or digest(entries[target_name]) != record["sha256"]
                    or entries[target_name] != entries[source_name]):
                raise ValueError("Native folder alias differs from its recorded source: " + record["path"])
            seen.add(target_name)
    unrecorded = {name for name in entries if name.startswith(THEME_DIRECTORY + "/") and name.endswith(".svg")} - seen
    if unrecorded:
        raise ValueError("Unrecorded SVG is excluded from publication: " + sorted(unrecorded)[0])
    # An SVG beside an authored PNG can change which artwork a desktop resolves.
    # Native upstream app SVGs remain in their own advertised Breeze directories.
    for name in entries:
        relative = PurePosixPath(name.removeprefix(THEME_DIRECTORY + "/"))
        if (name.startswith(THEME_DIRECTORY + "/") and len(relative.parts) == 4
                and relative.parts[0] == "local" and relative.parts[2] == "apps"
                and relative.stem in AUTHORED_KEYS and relative.suffix != ".png"):
            raise ValueError("Competing authored app variant is excluded: " + name)
    validate_artwork(entries)


def png_dimensions(data: bytes | VerifiedFile, name: str) -> tuple[int, int]:
    """Check the PNG header without requiring the rendering dependency."""
    if isinstance(data, VerifiedFile):
        data = data.header
    if (len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n")
            or data[8:16] != b"\x00\x00\x00\rIHDR"):
        raise ValueError("Selected artwork has no valid PNG header: " + name)
    dimensions = (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))
    if not all(dimensions):
        raise ValueError("Selected artwork has empty PNG dimensions: " + name)
    return dimensions


def validate_artwork(entries: dict[str, bytes | VerifiedFile]) -> None:
    """Require selected source bytes and all declared application size variants."""
    artwork = json.loads(entries["assets/icon-set-v1.0/artwork.json"])
    def public_metadata(value):
        if isinstance(value, dict):
            for child in value.values():
                public_metadata(child)
        elif isinstance(value, list):
            for child in value:
                public_metadata(child)
        elif isinstance(value, str):
            if value.startswith("/") or re.search(r"(?:^|[/\\])exec-[a-f0-9-]+\.png(?:$|\s)", value):
                raise ValueError("Private source filenames or host paths are excluded from artwork metadata")
    public_metadata(artwork)
    if (not isinstance(artwork, dict) or artwork.get("schema_version") != 1
            or artwork.get("theme_identity") != THEME or artwork.get("version") != VERSION):
        raise ValueError("Selected artwork metadata identity differs")
    if not isinstance(artwork.get("assets"), list) or not isinstance(artwork.get("rendered"), list):
        raise ValueError("Incomplete selected artwork records")
    expected_assets = {"assets/icon-set-v1.0/" + name: keys for name, keys in AUTHORED.items()}
    assets_seen = set()
    for record in artwork["assets"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("Incomplete selected artwork source record")
        name = safe_relative(record["path"]).as_posix()
        keys = record.get("icon_keys")
        if (name not in expected_assets or name in assets_seen or not isinstance(keys, list)
                or not all(isinstance(key, str) for key in keys)
                or len(keys) != len(set(keys)) or set(keys) != set(expected_assets[name])):
            raise ValueError("Selected artwork source or aliases differ: " + name)
        if name not in entries or record.get("sha256") != digest(entries[name]):
            raise ValueError("Selected artwork source hash differs: " + name)
        png_dimensions(entries[name], name)
        assets_seen.add(name)
    if assets_seen != set(expected_assets):
        raise ValueError("Selected artwork source records are incomplete")
    expected_rendered = {f"local/{size}/apps/{key}.png": size for size in SIZES for key in AUTHORED_KEYS}
    rendered_seen = set()
    for record in artwork["rendered"]:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("Incomplete rendered artwork record")
        relative = safe_relative(record["path"]).as_posix()
        name = THEME_DIRECTORY + "/" + relative
        if relative not in expected_rendered or relative in rendered_seen:
            raise ValueError("Unexpected or duplicate rendered artwork record: " + relative)
        if name not in entries or record.get("sha256") != digest(entries[name]):
            raise ValueError("Rendered artwork hash differs: " + relative)
        size = expected_rendered[relative]
        if png_dimensions(entries[name], relative) != (size, size):
            raise ValueError("Rendered artwork dimensions differ: " + relative)
        rendered_seen.add(relative)
    if rendered_seen != set(expected_rendered):
        raise ValueError("Rendered artwork size records are incomplete")
    # Aliases of one selected source must use the same rendered bytes at each size.
    for keys in AUTHORED.values():
        for size in SIZES:
            if len({digest(entries[THEME_DIRECTORY + f"/local/{size}/apps/{key}.png"]) for key in keys}) != 1:
                raise ValueError("Rendered artwork aliases differ at size " + str(size))


def verify_archive(path: Path) -> dict:
    """Check names, regular types and every manifested byte without extraction."""
    checksums = {}
    manifest = None
    seen = set()
    directories = set()
    entries = {}
    metadata = {"assets/icon-set-v1.0/PROVENANCE.json", "assets/icon-set-v1.0/artwork.json",
                "assets/icon-set-v1.0/folder-aliases.json", THEME_DIRECTORY + "/index.theme"}
    with tarfile.open(path, "r:gz") as archive:
        for member in archive:
            name = safe_relative(member.name).as_posix()
            if name in seen or not (name == ARCHIVE_ROOT or name.startswith(ARCHIVE_ROOT + "/")):
                raise ValueError("Unexpected or duplicate archive member: " + name)
            seen.add(name)
            if member.isdir():
                relative = "" if name == ARCHIVE_ROOT else name.removeprefix(ARCHIVE_ROOT + "/")
                if not allowed_directory(relative):
                    raise ValueError("Archive directory is outside the publication allowlist: " + relative)
                directories.add(relative)
                continue
            if not member.isfile() or member.issym() or member.islnk():
                raise ValueError("Archive contains a non-regular asset: " + name)
            relative = name.removeprefix(ARCHIVE_ROOT + "/")
            if not allowed_member(relative):
                raise ValueError("Archive asset is outside the publication allowlist: " + relative)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Unreadable archive asset: " + name)
            data = stream.read()
            if relative == "SHA256SUMS":
                manifest = data.decode()
            else:
                checksums[relative] = digest(data)
                entries[relative] = (data if relative in metadata else
                                     VerifiedFile(checksums[relative], data[:33]))
    expected = {}
    for line in (manifest or "").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if match is None or match[2] in expected:
            raise ValueError("Invalid or duplicate internal checksum entry")
        safe_relative(match[2])
        expected[match[2]] = match[1]
    if not expected or expected != checksums:
        raise ValueError("Archive bytes differ from the complete internal checksum manifest")
    validate_payload(entries, directories)
    if "README.md" not in entries or digest(entries["README.md"]) != digest(entries["ICONSET.md"]):
        raise ValueError("Archive README is missing or differs from the component guide")
    return {"files": len(checksums) + 1, "directories": len(directories),
            "sha256": digest(Path(path).read_bytes())}


def pack(output: Path, root: Path = ROOT) -> dict:
    entries, directories = collect(root)
    output = Path(output).expanduser().absolute()
    if any(component.is_symlink() for component in (output, *output.parents)):
        raise ValueError("Refusing symbolic-link archive output")
    sidecar = output.with_name(output.name + ".sha256")
    if sidecar.is_symlink():
        raise ValueError("Refusing symbolic-link checksum output")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".icon-set-v1.0-", suffix=".tar.gz", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as stream:
            with gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for name in [""] + sorted(directories):
                        info = tarfile.TarInfo(ARCHIVE_ROOT + ("/" + name if name else ""))
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        archive.addfile(info)
                    for name, data in sorted(entries.items()):
                        info = tarfile.TarInfo(ARCHIVE_ROOT + "/" + name)
                        info.mode = 0o755 if name in EXECUTABLES else 0o644
                        info.size = len(data)
                        archive.addfile(info, io.BytesIO(data))
        result = verify_archive(temporary)
        temporary.replace(output)
        sidecar.write_text(result["sha256"] + "  " + output.name + "\n")
        return {"archive": str(output), "checksum_file": str(sidecar), **result}
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / (ARCHIVE_ROOT + ".tar.gz"))
    parser.add_argument("--verify", type=Path, help="Verify an existing archive without writing")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(verify_archive(args.verify), sort_keys=True))
    else:
        print(json.dumps(pack(args.output), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
