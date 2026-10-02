#!/usr/bin/env python3
"""Receipt bounded v1.0 launcher icon changes in separate explicit scopes.

Only Icon in the main Desktop Entry group is changed. Launcher commands and
all other bytes are preserved in private backups and never included in output.
The credential-import scope changes only the named wizard's dialog-password
key; the existing absolute-icons scope remains exactly three launchers.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

THEME = "WitnessOpsIconsV1_0"
ABSOLUTE_IDS = frozenset({
    "blackbox-browser.desktop",
    "com.yubico.yubioath.desktop",
    "tuxedo-control-center.desktop",
})
WIZARD_ID = "witnessops-credential-import-wizard.desktop"
WIZARD_ICON_KEY = "witnessops-credential-import"
ALLOWED_IDS = ABSOLUTE_IDS | {WIZARD_ID}
SCOPES = {"absolute-icons": ABSOLUTE_IDS, "credential-import": frozenset({WIZARD_ID})}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OverrideError(ValueError):
    """An output-safe operation failure code; contains no launcher contents."""


def fail(code: str) -> None:
    raise OverrideError(code)


def safe_path(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(component.is_symlink() for component in (path, *path.parents)):
        fail("symlink_refused")
    return path


def read_bytes(path: Path) -> bytes:
    safe_path(path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        fail("missing_or_invalid_file")
    return path.read_bytes()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_identity(desktop_id: str, icon_key: str) -> None:
    if desktop_id not in ALLOWED_IDS or not SAFE_NAME.fullmatch(desktop_id) or ".." in desktop_id:
        fail("invalid_launcher_scope")
    expected_key = WIZARD_ICON_KEY if desktop_id == WIZARD_ID else desktop_id.removesuffix(".desktop")
    if not SAFE_NAME.fullmatch(icon_key) or ".." in icon_key or icon_key != expected_key:
        fail("invalid_icon_key")


def replace_main_icon(data: bytes, expected_icon: str, icon_key: str, desktop_id: str | None = None) -> bytes:
    # Iterate bytes so all unrelated bytes, line endings, comments, and action
    # groups survive unchanged. No command field is parsed or interpreted.
    lines = data.splitlines(keepends=True)
    active = False
    groups = 0
    icon_lines = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(b"[") and stripped.endswith(b"]"):
            active = stripped == b"[Desktop Entry]"
            if active:
                groups += 1
            continue
        if active:
            match = re.match(rb"^(\s*Icon=)([^\r\n]*)(\r?\n)?$", line)
            if match:
                icon_lines.append((index, match))
    if groups != 1 or len(icon_lines) != 1:
        fail("ambiguous_main_icon")
    index, match = icon_lines[0]
    try:
        raw_icon = match.group(2).decode("utf-8")
    except UnicodeDecodeError:
        fail("invalid_icon_encoding")
    escapes = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    decoded_icon = re.sub(r"\\([sntr\\])", lambda m: escapes[m.group(1)], raw_icon)
    named_migration = (desktop_id == WIZARD_ID and expected_icon == "dialog-password"
                       and icon_key == WIZARD_ICON_KEY)
    if decoded_icon != expected_icon or not (Path(expected_icon).is_absolute() or named_migration):
        fail("launcher_icon_drift")
    lines[index] = match.group(1) + icon_key.encode("ascii") + (match.group(3) or b"")
    return b"".join(lines)


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_path(path.parent)
    descriptor, temporary = tempfile.mkstemp(prefix=".launcher-icon-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_receipt(path: Path, state: dict) -> None:
    atomic_write(path, (json.dumps(state, indent=2) + "\n").encode("utf-8"), 0o600)


def load_inventory(path: Path, scope: str = "absolute-icons") -> list[dict]:
    if scope not in SCOPES:
        fail("invalid_launcher_scope")
    expected_ids = SCOPES[scope]
    try:
        value = json.loads(read_bytes(path))
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("invalid_inventory")
    if not isinstance(value, dict) or value.get("theme_identity") != THEME or not isinstance(value.get("apps"), list):
        fail("invalid_inventory")
    result = []
    seen = set()
    for row in value["apps"]:
        if not isinstance(row, dict):
            fail("invalid_inventory")
        if scope == "absolute-icons":
            if row.get("absolute_icon") is not True:
                continue
        else:
            if row.get("desktop_id") != WIZARD_ID:
                continue
            if row.get("absolute_icon") is not False or row.get("icon") != "dialog-password":
                fail("launcher_icon_drift")
            if row.get("icon_key") not in {"dialog-password", WIZARD_ICON_KEY}:
                fail("invalid_icon_key")
            row = {**row, "icon_key": WIZARD_ICON_KEY}
        desktop_id, icon_key = row.get("desktop_id"), row.get("icon_key")
        if not isinstance(desktop_id, str) or not isinstance(icon_key, str):
            fail("invalid_inventory")
        if desktop_id not in expected_ids:
            fail("invalid_launcher_scope")
        validate_identity(desktop_id, icon_key)
        if desktop_id in seen or not isinstance(row.get("desktop_file"), str) or not Path(row["desktop_file"]).is_absolute():
            fail("invalid_inventory")
        if Path(row["desktop_file"]).name != desktop_id:
            fail("invalid_inventory")
        if not isinstance(row.get("icon"), str) or (scope == "absolute-icons" and not Path(row["icon"]).is_absolute()) or not isinstance(row.get("desktop_sha256"), str) or not SHA256.fullmatch(row["desktop_sha256"]):
            fail("invalid_inventory")
        seen.add(desktop_id)
        result.append(row)
    # The helper belongs to this accepted v1.0 host scope. It cannot turn a
    # larger arbitrary app inventory into a mass desktop override operation.
    if seen != expected_ids:
        fail("incomplete_launcher_scope")
    return sorted(result, key=lambda row: row["desktop_id"])


def prepare(inventory_path: Path, data_home: Path, scope: str = "absolute-icons") -> list[dict]:
    data_home = safe_path(data_home)
    safe_path(data_home / "applications")
    result = []
    for app in load_inventory(inventory_path, scope):
        source = safe_path(Path(app["desktop_file"]))
        original = read_bytes(source)
        if digest(original) != app["desktop_sha256"]:
            fail("launcher_source_hash_drift")
        installed = replace_main_icon(original, app["icon"], app["icon_key"], app["desktop_id"])
        target = safe_path(data_home / "applications" / app["desktop_id"])
        existed = target.exists()
        previous = read_bytes(target) if existed else None
        if existed and source != target and previous != original:
            fail("edited_destination_refused")
        source_mode = stat.S_IMODE(source.stat().st_mode)
        mode = stat.S_IMODE(target.stat().st_mode) if existed else source_mode
        result.append({
            "desktop_id": app["desktop_id"], "icon_key": app["icon_key"],
            "source": str(source), "source_sha256": digest(original),
            "target": str(target), "destination_existed": existed,
            "previous_sha256": digest(previous) if previous is not None else None,
            "previous_mode": mode if existed else None,
            "installed_sha256": digest(installed), "installed_mode": mode,
            "source_bytes": original, "previous_bytes": previous,
            "installed_bytes": installed,
        })
    return result


def install(inventory_path: Path, data_home: Path, scope: str = "absolute-icons") -> dict:
    prepared = prepare(inventory_path, data_home, scope)
    data_home = safe_path(data_home)
    receipts = safe_path(data_home / "witnessops-theme" / "receipts")
    receipts.mkdir(parents=True, exist_ok=True)
    receipts.chmod(0o700)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]
    receipt = receipts / f"launcher-icons-v1.0-{stamp}.json"
    backup_dir = safe_path(receipts / f"launcher-icons-v1.0-{stamp}-backups")
    backup_dir.mkdir(mode=0o700)
    rows = []
    for index, row in enumerate(prepared):
        source_backup = f"source-{index}.desktop"
        atomic_write(backup_dir / source_backup, row["source_bytes"], 0o600)
        previous_backup = None
        if row["destination_existed"]:
            previous_backup = f"previous-{index}.desktop"
            atomic_write(backup_dir / previous_backup, row["previous_bytes"], 0o600)
        rows.append({key: value for key, value in row.items() if not key.endswith("_bytes")})
        rows[-1].update({"source_backup": source_backup, "previous_backup": previous_backup, "written": False})
    state = {"schema_version": 1, "theme_identity": THEME, "version": "1.0.0", "scope": scope, "data_home": str(data_home), "backup_dir": backup_dir.name, "rows": rows, "status": "preparing"}
    write_receipt(receipt, state)
    try:
        for row, payload in zip(rows, prepared):
            if digest(read_bytes(Path(row["source"]))) != row["source_sha256"]:
                fail("launcher_source_hash_drift")
            target = safe_path(Path(row["target"]))
            current = read_bytes(target) if target.exists() else None
            if (digest(current) if current is not None else None) != row["previous_sha256"]:
                fail("edited_destination_refused")
            atomic_write(target, payload["installed_bytes"], row["installed_mode"])
            row["written"] = True
            write_receipt(receipt, state)
        state["status"] = "installed"
        write_receipt(receipt, state)
        return {"status": "installed", "count": len(rows), "receipt": str(receipt)}
    except Exception:
        rolled_back = True
        for row, payload in reversed(list(zip(rows, prepared))):
            if not row["written"]:
                continue
            try:
                target = safe_path(Path(row["target"]))
                if digest(read_bytes(target)) != row["installed_sha256"]:
                    rolled_back = False
                    continue
                if row["destination_existed"]:
                    atomic_write(target, payload["previous_bytes"], row["previous_mode"])
                else:
                    target.unlink()
                row["written"] = False
            except (OSError, OverrideError):
                rolled_back = False
        state["status"] = "failed"
        state["rolled_back"] = rolled_back
        write_receipt(receipt, state)
        raise


def load_receipt(receipt: Path, data_home: Path) -> tuple[dict, Path]:
    receipt = safe_path(receipt)
    data_home = safe_path(data_home)
    receipts = safe_path(data_home / "witnessops-theme" / "receipts")
    if receipt.parent != receipts or not receipt.name.startswith("launcher-icons-v1.0-") or receipt.suffix != ".json":
        fail("invalid_receipt_path")
    try:
        state = json.loads(read_bytes(receipt))
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("invalid_receipt")
    if not isinstance(state, dict) or state.get("schema_version") != 1 or state.get("theme_identity") != THEME or state.get("data_home") != str(data_home):
        fail("receipt_target_mismatch")
    expected_backup_dir = receipt.stem + "-backups"
    if state.get("backup_dir") != expected_backup_dir:
        fail("invalid_receipt")
    backup_dir = safe_path(receipts / expected_backup_dir)
    # Older three-absolute-launcher receipts predate the scope field.
    scope = state.get("scope", "absolute-icons")
    if scope not in SCOPES:
        fail("invalid_receipt")
    expected_ids = SCOPES[scope]
    rows = state.get("rows")
    if not isinstance(rows, list) or len(rows) != len(expected_ids):
        fail("invalid_receipt")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("desktop_id"), str) or not isinstance(row.get("icon_key"), str):
            fail("invalid_receipt")
        validate_identity(row["desktop_id"], row["icon_key"])
        if row["desktop_id"] in seen or row.get("target") != str(data_home / "applications" / row["desktop_id"]):
            fail("invalid_receipt")
        seen.add(row["desktop_id"])
        for key in ("source_sha256", "installed_sha256"):
            if not isinstance(row.get(key), str) or not SHA256.fullmatch(row[key]):
                fail("invalid_receipt")
        if type(row.get("destination_existed")) is not bool or type(row.get("written")) is not bool:
            fail("invalid_receipt")
        for key in ("installed_mode", "previous_mode"):
            mode = row.get(key)
            if mode is not None and (type(mode) is not int or not 0 <= mode <= 0o777):
                fail("invalid_receipt")
        if row.get("installed_mode") is None:
            fail("invalid_receipt")
        if row["destination_existed"] and row.get("previous_mode") is None:
            fail("invalid_receipt")
        for key, sha_key in (("source_backup", "source_sha256"), ("previous_backup", "previous_sha256")):
            filename = row.get(key)
            if key == "previous_backup" and not row["destination_existed"]:
                if filename is not None or row.get(sha_key) is not None:
                    fail("invalid_receipt")
                continue
            if not isinstance(filename, str) or not SAFE_NAME.fullmatch(filename) or ".." in filename or not isinstance(row.get(sha_key), str) or not SHA256.fullmatch(row[sha_key]):
                fail("invalid_receipt")
            if digest(read_bytes(backup_dir / filename)) != row[sha_key]:
                fail("backup_hash_drift")
    if seen != expected_ids:
        fail("invalid_receipt")
    return state, backup_dir


def verify(receipt: Path, data_home: Path) -> dict:
    state, _ = load_receipt(receipt, data_home)
    if state.get("status") != "installed":
        fail("receipt_not_installed")
    for row in state["rows"]:
        if not row["written"] or digest(read_bytes(Path(row["target"]))) != row["installed_sha256"]:
            fail("installed_launcher_hash_drift")
    return {"status": "verified", "count": len(state["rows"]), "receipt": str(receipt.absolute())}


def restore(receipt: Path, data_home: Path) -> dict:
    state, backup_dir = load_receipt(receipt, data_home)
    if state.get("status") not in {"installed", "restoring", "restored"}:
        fail("receipt_not_installed")
    rows = state["rows"]
    if state["status"] == "restored":
        for row in rows:
            target = safe_path(Path(row["target"]))
            current = read_bytes(target) if target.exists() else None
            if (digest(current) if current is not None else None) != row["previous_sha256"]:
                fail("restored_launcher_hash_drift")
        return {"status": "restored", "count": len(rows), "receipt": str(receipt.absolute())}
    # Preflight every destination before changing any. A restoring receipt can
    # resume an interrupted restore only when each launcher is exactly either
    # the installed bytes or its saved original bytes. User edits are refused.
    for row in rows:
        target = safe_path(Path(row["target"]))
        current = read_bytes(target) if target.exists() else None
        current_sha = digest(current) if current is not None else None
        if current_sha == row["installed_sha256"]:
            if not row["written"] and state["status"] == "installed":
                fail("invalid_receipt")
            row["written"] = True
        elif state["status"] == "restoring" and current_sha == row["previous_sha256"]:
            row["written"] = False
        else:
            fail("installed_launcher_hash_drift")
    state["status"] = "restoring"
    write_receipt(receipt, state)
    for row in rows:
        if not row["written"]:
            continue
        target = safe_path(Path(row["target"]))
        if digest(read_bytes(target)) != row["installed_sha256"]:
            fail("edited_destination_refused")
        if row["destination_existed"]:
            atomic_write(target, read_bytes(backup_dir / row["previous_backup"]), row["previous_mode"])
        else:
            target.unlink()
        row["written"] = False
        write_receipt(receipt, state)
    state["status"] = "restored"
    write_receipt(receipt, state)
    return {"status": "restored", "count": len(rows), "receipt": str(receipt.absolute())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "install", "verify", "restore"])
    parser.add_argument("--inventory", type=Path, help="Private v1.0 host inventory required for plan/install")
    parser.add_argument("--scope", choices=sorted(SCOPES), default="absolute-icons",
                        help="Plan/install scope; verify/restore read the receipt's saved scope")
    parser.add_argument("--backup", type=Path, help="Receipt required for verify/restore")
    parser.add_argument("--data-home", type=Path, default=Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))))
    parser.add_argument("--yes", action="store_true", help="Explicitly authorize install/restore mutations")
    args = parser.parse_args(argv)
    try:
        if args.action in {"install", "restore"} and not args.yes:
            fail("explicit_yes_required")
        data_home = safe_path(args.data_home)
        if args.action in {"plan", "install"}:
            if args.inventory is None:
                fail("inventory_required")
            if args.action == "plan":
                result = {"status": "planned", "count": len(prepare(args.inventory, data_home, args.scope))}
            else:
                result = install(args.inventory, data_home, args.scope)
        else:
            if args.backup is None:
                fail("receipt_required")
            result = verify(args.backup, data_home) if args.action == "verify" else restore(args.backup, data_home)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Filesystem exception text can contain unexpected filenames. Output
        # only stable status codes, never launcher bytes or command values.
        status = str(exc) if isinstance(exc, OverrideError) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": status}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
