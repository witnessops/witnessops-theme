import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("launcher_overrides", ROOT / "tools/launcher_icon_overrides.py")
OVERRIDES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OVERRIDES)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LauncherIconOverridesTest(unittest.TestCase):
    def fixtures(self, root):
        data = root / "data"
        source = root / "system"
        source.mkdir()
        (data / "applications").mkdir(parents=True)
        rows, originals = [], {}
        for index, desktop_id in enumerate(sorted(OVERRIDES.ABSOLUTE_IDS)):
            # Mix an existing user launcher with system launchers. Desktop action
            # Icon and secret-looking Exec strings must retain their exact bytes.
            path = (data / "applications" if index == 0 else source) / desktop_id
            icon = str(root / f"app-{index}.png")
            original = (f"# comment\r\n[Desktop Entry]\r\nType=Application\r\nName=Example\r\nIcon={icon}\r\nExec=private-value-$HOME\r\n\r\n[Desktop Action Other]\r\nIcon=action-icon\r\nExec=other-command\r\n").encode()
            path.write_bytes(original)
            path.chmod(0o755 if index == 0 else 0o644)
            originals[desktop_id] = (path, original)
            rows.append({"desktop_id": desktop_id, "icon_key": desktop_id.removesuffix(".desktop"), "absolute_icon": True, "desktop_file": str(path), "desktop_sha256": sha(original), "icon": icon})
        inventory = root / "inventory.json"
        inventory.write_text(json.dumps({"theme_identity": OVERRIDES.THEME, "apps": rows}))
        return data, inventory, rows, originals

    def snapshot(self, root):
        return {path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in root.rglob("*") if path.is_file()}

    def foreign_owner(self, path, owner_uid=None):
        original_stat = Path.stat
        foreign_uid = os.getuid() + 1 if owner_uid is None else owner_uid

        def pretend_foreign(candidate, *args, **kwargs):
            result = original_stat(candidate, *args, **kwargs)
            if candidate == path:
                fields = list(result)
                fields[4] = foreign_uid
                return os.stat_result(fields)
            return result

        return mock.patch.object(Path, "stat", new=pretend_foreign)

    def test_privileged_identity_refuses_install_restore_and_atomic_write_without_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, _, _ = self.fixtures(root)
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            before = self.snapshot(root)
            uid = os.getuid()
            for real_uid, effective_uid in ((0, 0), (uid, 0), (uid, uid + 1), (0, uid)):
                with self.subTest(real_uid=real_uid, effective_uid=effective_uid):
                    with mock.patch.object(OVERRIDES.os, "getuid", return_value=real_uid), \
                            mock.patch.object(OVERRIDES.os, "geteuid", return_value=effective_uid):
                        for operation in (lambda: OVERRIDES.install(inventory, data),
                                          lambda: OVERRIDES.restore(receipt, data),
                                          lambda: OVERRIDES.atomic_write(root / "unexpected.desktop", b"x", 0o600)):
                            with self.assertRaisesRegex(OVERRIDES.OverrideError, "desktop_user_required"):
                                operation()
                        for action, input_flag, input_path in (("install", "--inventory", inventory),
                                                              ("restore", "--backup", receipt)):
                            output = io.StringIO()
                            with contextlib.redirect_stderr(output):
                                code = OVERRIDES.main([action, "--yes", input_flag, str(input_path),
                                                       "--data-home", str(data)])
                            self.assertEqual(code, 1)
                            self.assertEqual(json.loads(output.getvalue())["reason"], "desktop_user_required")
                    self.assertEqual(self.snapshot(root), before)

    def test_foreign_owned_install_destinations_refused_before_receipts_or_launcher_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, rows, _ = self.fixtures(root)
            receipts = data / "witnessops-theme/receipts"
            receipts.mkdir(parents=True)
            before = self.snapshot(root)
            destinations = (data, data / "applications", receipts.parent, receipts,
                            data / "applications" / rows[0]["desktop_id"])
            for destination in destinations:
                with self.subTest(destination=destination.relative_to(root)):
                    with self.foreign_owner(destination):
                        with self.assertRaisesRegex(OVERRIDES.OverrideError, "user_owned_destination_required"):
                            OVERRIDES.install(inventory, data)
                    self.assertEqual(self.snapshot(root), before)
                    self.assertEqual(list(receipts.iterdir()), [])

    def test_absent_data_home_with_foreign_owned_closest_ancestor_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, inventory, _, _ = self.fixtures(root)
            data = root / "new-xdg/share"
            before = self.snapshot(root)
            with self.foreign_owner(root):
                with self.assertRaisesRegex(OVERRIDES.OverrideError, "user_owned_destination_required"):
                    OVERRIDES.install(inventory, data)
            self.assertFalse(data.parent.exists())
            self.assertEqual(self.snapshot(root), before)

    def test_foreign_owned_restore_targets_receipts_and_backups_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, rows, _ = self.fixtures(root)
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            state = json.loads(receipt.read_text())
            backups = receipt.parent / state["backup_dir"]
            first = state["rows"][0]
            destinations = (data, data / "applications", receipt.parent.parent, receipt.parent,
                            receipt, backups, backups / first["source_backup"],
                            backups / first["previous_backup"],
                            data / "applications" / rows[-1]["desktop_id"])
            before = self.snapshot(root)
            for destination in destinations:
                with self.subTest(destination=destination.relative_to(root)):
                    with self.foreign_owner(destination):
                        with self.assertRaisesRegex(OVERRIDES.OverrideError, "user_owned_destination_required"):
                            OVERRIDES.restore(receipt, data)
                    self.assertEqual(self.snapshot(root), before)

    def test_parent_traversal_refused_before_install_or_restore_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, _, _ = self.fixtures(root)
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            before = self.snapshot(root)
            traversing_data = data / "applications/.."
            traversing_receipt = receipt.parent / "../receipts" / receipt.name
            for operation in (lambda: OVERRIDES.install(inventory, traversing_data),
                              lambda: OVERRIDES.restore(receipt, traversing_data),
                              lambda: OVERRIDES.restore(traversing_receipt, data)):
                with self.assertRaisesRegex(OVERRIDES.OverrideError, "parent_traversal_refused"):
                    operation()
                self.assertEqual(self.snapshot(root), before)

    def test_read_only_plan_and_verify_remain_available_without_mutation_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, _, _ = self.fixtures(root)
            before = self.snapshot(root)
            with mock.patch.object(OVERRIDES.os, "getuid", return_value=0), \
                    mock.patch.object(OVERRIDES.os, "geteuid", return_value=0), self.foreign_owner(data):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = OVERRIDES.main(["plan", "--inventory", str(inventory), "--data-home", str(data)])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.getvalue())["status"], "planned")
            self.assertEqual(self.snapshot(root), before)
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            before = self.snapshot(root)
            with mock.patch.object(OVERRIDES.os, "getuid", return_value=0), \
                    mock.patch.object(OVERRIDES.os, "geteuid", return_value=0), self.foreign_owner(data):
                self.assertEqual(OVERRIDES.verify(receipt, data)["status"], "verified")
            self.assertEqual(self.snapshot(root), before)

    def test_new_user_owned_data_home_and_system_sources_still_install_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, inventory, rows, originals = self.fixtures(root)
            data = root / "new-user-owned/xdg/share"
            self.assertFalse(data.exists())
            system_source = originals[rows[-1]["desktop_id"]][0]
            # Reading a root-owned system launcher is permitted; the new
            # launcher, private backup, and receipt remain user-owned.
            with self.foreign_owner(system_source, owner_uid=0):
                receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            self.assertEqual(OVERRIDES.verify(receipt, data)["count"], 3)
            self.assertEqual(OVERRIDES.restore(receipt, data)["status"], "restored")
            self.assertEqual(list((data / "applications").iterdir()), [])
            for source, original in originals.values():
                self.assertEqual(source.read_bytes(), original)

    def test_plan_install_verify_restore_changes_only_main_icon(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, rows, originals = self.fixtures(root)
            self.assertEqual(len(OVERRIDES.prepare(inventory, data)), 3)
            self.assertFalse((data / "witnessops-theme").exists())
            installed = OVERRIDES.install(inventory, data)
            receipt = Path(installed["receipt"])
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            for row in rows:
                _, original = originals[row["desktop_id"]]
                expected = original.replace(f"Icon={row['icon']}\r\n".encode(), f"Icon={row['icon_key']}\r\n".encode(), 1)
                self.assertEqual((data / "applications" / row["desktop_id"]).read_bytes(), expected)
            self.assertEqual(OVERRIDES.verify(receipt, data)["status"], "verified")
            self.assertEqual(OVERRIDES.restore(receipt, data)["status"], "restored")
            for index, desktop_id in enumerate(sorted(OVERRIDES.ABSOLUTE_IDS)):
                path, original = originals[desktop_id]
                self.assertEqual(path.read_bytes(), original)
                if index != 0:
                    self.assertFalse((data / "applications" / desktop_id).exists())
            self.assertEqual(OVERRIDES.restore(receipt, data)["status"], "restored")

    def test_source_drift_and_existing_destination_collision_refused(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, rows, originals = self.fixtures(Path(td))
            source = originals[rows[1]["desktop_id"]][0]
            source.write_bytes(source.read_bytes() + b"# newer edit\n")
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "source_hash_drift"):
                OVERRIDES.prepare(inventory, data)
            source.write_bytes(originals[rows[1]["desktop_id"]][1])
            target = data / "applications" / rows[1]["desktop_id"]
            target.write_bytes(b"user customization")
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "edited_destination_refused"):
                OVERRIDES.prepare(inventory, data)
            self.assertEqual(target.read_bytes(), b"user customization")

    def test_restore_refuses_post_install_edits_before_changing_any_target(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, rows, _ = self.fixtures(Path(td))
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            targets = [data / "applications" / row["desktop_id"] for row in rows]
            targets[-1].write_bytes(targets[-1].read_bytes() + b"# user edit\n")
            before = {path: path.read_bytes() for path in targets}
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "installed_launcher_hash_drift"):
                OVERRIDES.restore(receipt, data)
            self.assertEqual({path: path.read_bytes() for path in targets}, before)

    def test_interrupted_restore_resumes_only_exact_saved_or_installed_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, rows, originals = self.fixtures(Path(td))
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            state = json.loads(receipt.read_text())
            state["status"] = "restoring"
            # Simulate interruption after restoring bytes but before updating
            # the receipt's per-file progress field.
            restored_id = rows[0]["desktop_id"]
            (data / "applications" / restored_id).write_bytes(originals[restored_id][1])
            receipt.write_text(json.dumps(state))
            self.assertEqual(OVERRIDES.restore(receipt, data)["status"], "restored")
            self.assertEqual((data / "applications" / restored_id).read_bytes(), originals[restored_id][1])
            for row in rows[1:]:
                self.assertFalse((data / "applications" / row["desktop_id"]).exists())

    def test_symlinks_and_unsafe_identity_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data, inventory, rows, originals = self.fixtures(root)
            source = originals[rows[1]["desktop_id"]][0]
            actual = root / "actual.desktop"
            source.rename(actual)
            source.symlink_to(actual)
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "symlink_refused"):
                OVERRIDES.prepare(inventory, data)
            rows[1]["desktop_id"] = "../escape.desktop"
            inventory.write_text(json.dumps({"theme_identity": OVERRIDES.THEME, "apps": rows}))
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "invalid_launcher_scope"):
                OVERRIDES.prepare(inventory, data)

    def test_explicit_yes_required_and_cli_output_omits_contents(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, _, _ = self.fixtures(Path(td))
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                code = OVERRIDES.main(["install", "--inventory", str(inventory), "--data-home", str(data)])
            self.assertEqual(code, 1)
            self.assertIn("explicit_yes_required", output.getvalue())
            self.assertFalse((data / "witnessops-theme").exists())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = OVERRIDES.main(["install", "--yes", "--inventory", str(inventory), "--data-home", str(data)])
            self.assertEqual(code, 0)
            self.assertNotIn("private-value", output.getvalue())
            self.assertNotIn("Exec", output.getvalue())
            self.assertEqual(json.loads(output.getvalue())["count"], 3)

    def wizard_fixture(self, root, existing_user=True):
        data, inventory, rows, originals = self.fixtures(root)
        source = (data / "applications" if existing_user else root / "system") / OVERRIDES.WIZARD_ID
        original = (b"# wizard comments\r\n[Desktop Entry]\r\nType=Application\r\n"
                    b"Name=Credential Import Wizard\r\nIcon=dialog-password\r\n"
                    b"Exec=private-wizard-command-$HOME\r\n\r\n"
                    b"[Desktop Action Other]\r\nIcon=dialog-password\r\nExec=private-action\r\n")
        source.write_bytes(original)
        source.chmod(0o755)
        wizard = {"desktop_id": OVERRIDES.WIZARD_ID, "icon_key": "dialog-password",
                  "absolute_icon": False, "desktop_file": str(source),
                  "desktop_sha256": sha(original), "icon": "dialog-password"}
        other = data / "applications/other-password-dialog.desktop"
        other.write_bytes(original.replace(b"Name=Credential Import Wizard", b"Name=Other Password Dialog"))
        rows.extend([wizard, {"desktop_id": other.name, "icon_key": "dialog-password", "absolute_icon": False,
                             "desktop_file": str(other), "desktop_sha256": sha(other.read_bytes()), "icon": "dialog-password"}])
        inventory.write_text(json.dumps({"theme_identity": OVERRIDES.THEME, "apps": rows}))
        return data, inventory, wizard, source, original, other

    def test_wizard_scope_changes_only_named_launcher_and_has_independent_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, wizard, source, original, other = self.wizard_fixture(Path(td))
            before_other = other.read_bytes()
            absolute_plan = OVERRIDES.prepare(inventory, data)
            self.assertEqual(len(absolute_plan), 3)
            prepared = OVERRIDES.prepare(inventory, data, "credential-import")
            self.assertEqual(len(prepared), 1)
            self.assertEqual(prepared[0]["icon_key"], OVERRIDES.WIZARD_ICON_KEY)
            absolute_install = OVERRIDES.install(inventory, data)
            wizard_install = OVERRIDES.install(inventory, data, "credential-import")
            self.assertNotEqual(absolute_install["receipt"], wizard_install["receipt"])
            expected = original.replace(b"Icon=dialog-password\r\n",
                                        b"Icon=witnessops-credential-import\r\n", 1)
            self.assertEqual(source.read_bytes(), expected)
            self.assertEqual(other.read_bytes(), before_other)
            receipt = Path(wizard_install["receipt"])
            self.assertEqual(json.loads(receipt.read_text())["scope"], "credential-import")
            self.assertEqual(OVERRIDES.verify(receipt, data)["count"], 1)
            self.assertEqual(OVERRIDES.restore(receipt, data)["count"], 1)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(source.stat().st_mode & 0o777, 0o755)
            self.assertEqual(other.read_bytes(), before_other)
            self.assertEqual(OVERRIDES.verify(Path(absolute_install["receipt"]), data)["count"], 3)

    def test_wizard_system_source_copied_and_normalized_inventory_key_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, wizard, source, original, _ = self.wizard_fixture(Path(td), existing_user=False)
            value = json.loads(inventory.read_text())
            for row in value["apps"]:
                if row["desktop_id"] == OVERRIDES.WIZARD_ID:
                    row["icon_key"] = OVERRIDES.WIZARD_ICON_KEY
            inventory.write_text(json.dumps(value))
            receipt = Path(OVERRIDES.install(inventory, data, "credential-import")["receipt"])
            target = data / "applications" / OVERRIDES.WIZARD_ID
            self.assertIn(b"Icon=witnessops-credential-import\r\n", target.read_bytes())
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(OVERRIDES.restore(receipt, data)["count"], 1)
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), original)

    def test_wizard_named_icon_drift_and_post_install_edits_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, wizard, source, original, _ = self.wizard_fixture(Path(td))
            source.write_bytes(original.replace(b"Icon=dialog-password", b"Icon=dialog-information", 1))
            value = json.loads(inventory.read_text())
            for row in value["apps"]:
                if row["desktop_id"] == OVERRIDES.WIZARD_ID:
                    row["desktop_sha256"] = sha(source.read_bytes())
            inventory.write_text(json.dumps(value))
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "launcher_icon_drift"):
                OVERRIDES.prepare(inventory, data, "credential-import")
            source.write_bytes(original)
            wizard["desktop_sha256"] = sha(original)
            inventory.write_text(json.dumps({"theme_identity": OVERRIDES.THEME, "apps": [wizard]}))
            receipt = Path(OVERRIDES.install(inventory, data, "credential-import")["receipt"])
            source.write_bytes(source.read_bytes() + b"# user edit\n")
            before = source.read_bytes()
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "installed_launcher_hash_drift"):
                OVERRIDES.restore(receipt, data)
            self.assertEqual(source.read_bytes(), before)

    def test_legacy_three_launcher_receipt_remains_verifiable(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, _, _ = self.fixtures(Path(td))
            receipt = Path(OVERRIDES.install(inventory, data)["receipt"])
            state = json.loads(receipt.read_text())
            state.pop("scope")
            receipt.write_text(json.dumps(state))
            self.assertEqual(OVERRIDES.verify(receipt, data)["count"], 3)
            self.assertEqual(OVERRIDES.restore(receipt, data)["count"], 3)

    def test_wizard_scope_refuses_another_password_dialog_as_source(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, wizard, _, _, other = self.wizard_fixture(Path(td))
            wizard["desktop_file"] = str(other)
            wizard["desktop_sha256"] = sha(other.read_bytes())
            inventory.write_text(json.dumps({"theme_identity": OVERRIDES.THEME, "apps": [wizard]}))
            with self.assertRaisesRegex(OVERRIDES.OverrideError, "invalid_inventory"):
                OVERRIDES.prepare(inventory, data, "credential-import")

    def test_credential_import_scope_cli_plan_is_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            data, inventory, _, source, original, _ = self.wizard_fixture(Path(td))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = OVERRIDES.main(["plan", "--scope", "credential-import", "--inventory", str(inventory),
                                       "--data-home", str(data)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue()), {"status": "planned", "count": 1})
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((data / "witnessops-theme").exists())


if __name__ == "__main__":
    unittest.main()
