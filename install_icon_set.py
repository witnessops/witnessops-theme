#!/usr/bin/env python3
"""Install or restore the user-scoped WitnessOps Icon Set v1.0."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
THEME = "WitnessOpsIconsV1_0"
VERSION = "1.0.0"
SOURCE = ROOT / "packages/icon-set-v1.0" / THEME
RECEIPT_PREFIX = "icon-set-v1.0-"


def safe(path):
    path = Path(path).expanduser()
    if ".." in path.parts:
        raise ValueError("Parent traversal is not allowed: " + str(path))
    path = path.absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Refusing symbolic-link path: " + str(path))
    return path


def user_destination(path):
    path = safe(path)
    existing = next(item for item in (path, *path.parents) if item.exists())
    if existing == Path("/") or existing.stat().st_uid != os.getuid():
        raise ValueError("Destination must be inside a user-owned directory: " + str(path))
    if not existing.is_dir():
        raise ValueError("Destination ancestor is not a directory: " + str(existing))
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    root = safe(root)
    if not root.is_dir():
        raise ValueError("Missing icon directory: " + str(root))
    files = {}
    for path in sorted(root.rglob("*")):
        safe(path)
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("Refusing non-regular icon asset: " + str(path))
        files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return files


def expected(source):
    files = inventory(source)
    if "index.theme" not in files or len(files) < 2:
        raise ValueError("Incomplete icon set: index.theme and icon assets are required")
    if "[Icon Theme]" not in (source / "index.theme").read_text():
        raise ValueError("Invalid index.theme")
    return files


def configuration(config):
    path = safe(config / "kdeglobals")
    if path.exists() and not path.is_file():
        raise ValueError("kdeglobals is not a regular file")
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    section, theme = [], None
    in_icons, immutable = False, False
    for line in lines:
        text = line.strip()
        if text.startswith("["):
            in_icons = re.fullmatch(r"\[Icons\](?:\[\$[^\]]+\])*", text) is not None
            if in_icons and "[$i]" in text:
                immutable = True
        if in_icons:
            section.append(line)
            match = re.fullmatch(r"Theme(?:\[\$[^\]]+\])*=(.*)", text)
            if match:
                theme = match.group(1)
                immutable |= "[$i]" in text.split("=", 1)[0]
    return {"config_existed": path.exists(), "icons_section": "".join(section),
            "theme_present": theme is not None, "theme": theme,
            "immutable": immutable}


def write_receipt(path, state):
    path = safe(path)
    fd, temporary = tempfile.mkstemp(prefix=".icon-set-receipt-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def activation_environment(data, config):
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") or not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise ValueError("No desktop session; use --no-activate for a rehearsal")
    if not shutil.which("kwriteconfig6"):
        raise ValueError("kwriteconfig6 is unavailable")
    if configuration(config)["immutable"]:
        raise ValueError("The KDE Icons section or Theme key is immutable")
    return {**os.environ, "XDG_DATA_HOME": str(data), "XDG_CONFIG_HOME": str(config)}


def write_theme(config, theme, env):
    path = safe(config / "kdeglobals")
    config.mkdir(parents=True, exist_ok=True)
    command = ["kwriteconfig6", "--file", str(path), "--group", "Icons",
               "--key", "Theme", "--notify"]
    if theme is None:
        command.extend(["--delete", ""])
    else:
        command.append(theme)
    # KDE's native --notify sends the configuration change notification over
    # the session bus. The timeout bounds both the write and notification.
    subprocess.run(command, env=env, check=True, capture_output=True, text=True, timeout=15)
    if configuration(config)["theme"] != theme:
        raise ValueError("KDE Icons.Theme readback differs from the requested selection")


def remove_created(target, files, payload):
    """Check every owned file before removing any; preserve unrecorded files."""
    if not files:
        return
    target = safe(target)
    if not target.exists():
        return
    if not target.is_dir():
        raise ValueError("Installed theme directory was replaced")
    paths = [safe(target / name) for name in files]
    for name, path in zip(files, paths):
        if path.exists() and (not path.is_file() or digest(path.read_bytes()) != payload[name]):
            raise ValueError("Icon asset changed after installation; refusing removal: " + name)
    for path in paths:
        path.unlink(missing_ok=True)
    parents = {parent for path in paths for parent in path.parents if target in parent.parents}
    for path in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        target.rmdir()
    except OSError:
        pass


def install(source, data, config, no_activate):
    payload = expected(source)
    target = safe(data / "icons" / THEME)
    if target.exists() and inventory(target) != payload:
        raise ValueError("Existing icon set differs; refusing overwrite: " + str(target))
    previous = configuration(config)
    env = None if no_activate else activation_environment(data, config)
    receipts = safe(data / "witnessops-theme/receipts")
    receipts.mkdir(parents=True, exist_ok=True)
    receipts.chmod(0o700)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    receipt = receipts / (RECEIPT_PREFIX + timestamp + ".json")
    state = {"schema_version": 1, "kind": "icon-set", "version": VERSION,
             "theme": THEME, "source": str(source), "data_home": str(data),
             "config_home": str(config), "payload": payload,
             "previous_theme": previous["theme"], "configuration_backup": previous,
             "created_files": [], "activated": False, "activation_attempted": False,
             "status": "preparing"}
    write_receipt(receipt, state)
    try:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.mkdir()  # Exclusive creation refuses a newly appearing destination.
            state["created_files"] = sorted(payload)
            write_receipt(receipt, state)
            for name, checksum in payload.items():
                content = safe(source / name).read_bytes()
                if digest(content) != checksum:
                    raise ValueError("Source icon asset changed during installation: " + name)
                destination = safe(target / name)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    stream.write(content)
        if inventory(target) != payload:
            raise ValueError("Installed icon payload differs from the source")
        if env is not None:
            state["activation_attempted"] = True
            write_receipt(receipt, state)
            write_theme(config, THEME, env)
            state["activated"] = True
        state["status"] = "installed"
        write_receipt(receipt, state)
    except Exception as exc:
        state["status"] = "failed"
        state["error"] = str(exc)
        try:
            if state["activation_attempted"]:
                current = configuration(config)["theme"]
                if current not in (THEME, previous["theme"]):
                    raise ValueError("Icons.Theme changed during installation; preserving that selection")
                if current != previous["theme"]:
                    write_theme(config, previous["theme"], env)
            remove_created(target, state["created_files"], payload)
            state["status"] = "rolled_back"
        except Exception as rollback_error:
            state["rollback_error"] = str(rollback_error)
        write_receipt(receipt, state)
        raise
    print("ICON SET v1.0 INSTALLED; activated=" + str(state["activated"]))
    print("Theme: " + THEME)
    print("Receipt: " + str(receipt))
    return 0


def load_receipt(path, data, config):
    state = json.loads(safe(path).read_text())
    if (not isinstance(state, dict) or state.get("schema_version") != 1 or state.get("kind") != "icon-set"
            or state.get("version") != VERSION or state.get("theme") != THEME
            or state.get("data_home") != str(data) or state.get("config_home") != str(config)):
        raise ValueError("Invalid icon-set receipt or destination")
    payload, created = state.get("payload"), state.get("created_files")
    if not isinstance(payload, dict) or not isinstance(created, list):
        raise ValueError("Invalid icon-set receipt payload")
    for name, checksum in payload.items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts
                or not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum)):
            raise ValueError("Invalid asset in icon-set receipt")
    if not set(created) <= set(payload) or len(created) != len(set(created)):
        raise ValueError("Invalid created-asset list")
    backup = state.get("configuration_backup")
    if not isinstance(backup, dict) or not isinstance(backup.get("theme_present"), bool):
        raise ValueError("Invalid Icons configuration backup")
    theme = backup.get("theme")
    if (theme is not None and (not isinstance(theme, str) or any(ord(c) < 32 for c in theme))
            or backup["theme_present"] != (theme is not None)):
        raise ValueError("Invalid prior icon selection")
    return state


def restore(receipt, data, config, no_activate):
    state = load_receipt(receipt, data, config)
    if state.get("status") in ("restored", "rolled_back"):
        print("ICON SET v1.0 ALREADY RESTORED")
        return 0
    target = safe(data / "icons" / THEME)
    # Validate all owned paths before changing the selected theme.
    for name in state["created_files"]:
        path = safe(target / name)
        if path.exists() and (not path.is_file() or digest(path.read_bytes()) != state["payload"][name]):
            raise ValueError("Icon asset changed after installation; refusing restoration: " + name)
    active = state.get("activated") or state.get("activation_attempted")
    if active:
        if no_activate:
            raise ValueError("This receipt changed Icons.Theme; restore without --no-activate")
        current = configuration(config)["theme"]
        prior = state["configuration_backup"]["theme"]
        if current not in (THEME, prior):
            raise ValueError("Icons.Theme changed after installation; refusing to replace the new selection")
        if current == THEME:
            write_theme(config, prior, activation_environment(data, config))
    remove_created(target, state["created_files"], state["payload"])
    state["status"] = "restored"
    write_receipt(receipt, state)
    print("ICON SET v1.0 RESTORED; prior sets and unrelated settings preserved")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", default="plan", choices=("plan", "install", "verify", "restore"))
    parser.add_argument("--source", type=Path, default=SOURCE, help="Explicit icon theme source directory")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--backup", type=Path, help="Receipt JSON for restoration")
    parser.add_argument("--data-home", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))))
    parser.add_argument("--config-home", type=Path, default=Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))))
    args = parser.parse_args(argv)
    try:
        if os.geteuid() == 0:
            raise ValueError("Run as the desktop user, not root")
        data, config = user_destination(args.data_home), user_destination(args.config_home)
        safe(data / "icons")
        source = safe(args.source)
        if args.action == "restore":
            if not args.backup:
                raise ValueError("Restore requires --backup RECEIPT.json")
            return restore(safe(args.backup), data, config, args.no_activate)
        payload = expected(source)
        if args.action == "plan":
            print("ICON SET v1.0 PLAN; no changes made")
            print("Theme: " + THEME + "; files=" + str(len(payload)))
            print("Destination: " + str(data / "icons" / THEME))
            print("Selection: " + ("unchanged" if args.no_activate else "KDE Icons.Theme only"))
            return 0
        if args.action == "verify":
            if inventory(data / "icons" / THEME) != payload:
                raise ValueError("Installed icon-set payload mismatch")
            if not args.no_activate and configuration(config)["theme"] != THEME:
                raise ValueError("WitnessOps Icon Set v1.0 is not selected")
            print("ICON SET v1.0 PAYLOAD AND REQUESTED SELECTION CHECKS PASS")
            return 0
        if not args.yes:
            if not sys.stdin.isatty() or input("Install WitnessOps Icon Set v1.0? [y/N] ").strip().lower() not in ("y", "yes"):
                print("No changes made.")
                return 0
        return install(source, data, config, args.no_activate)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("ICON SET OPERATION FAILED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
