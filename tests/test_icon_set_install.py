import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
THEME = "WitnessOpsIconsV1_0"


class IconSetInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        (self.source / "scalable/apps").mkdir(parents=True)
        (self.source / "index.theme").write_text("[Icon Theme]\nName=WitnessOps Icon Set v1.0\n")
        (self.source / "scalable/apps/chatgpt.svg").write_text("<svg>recognizable app</svg>\n")
        (self.source / "scalable/apps/folder.svg").write_text("<svg>recognizable folder</svg>\n")
        self.data = self.root / "data"
        self.config = self.root / "config"
        self.config.mkdir()
        self.globals = self.config / "kdeglobals"
        self.before = "[General]\nfont=Keep me\n[Icons]\nTheme=breeze-dark\nSize=24\n[Colors:Window]\nBackgroundNormal=1,2,3\n"
        self.globals.write_text(self.before)
        self.target = self.data / "icons" / THEME

    def command(self, action, *args, active=False, env=None):
        command = [sys.executable, str(ROOT / "install_icon_set.py"), action,
                   "--source", str(self.source), "--data-home", str(self.data),
                   "--config-home", str(self.config), *args]
        if not active:
            command.append("--no-activate")
        return subprocess.run(command, capture_output=True, text=True, env=env)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def receipts(self):
        return sorted((self.data / "witnessops-theme/receipts").glob("icon-set-v1.0-*.json"))

    def test_plan_and_repeat_restore_preserve_old_sets_and_unowned_files(self):
        self.assert_success(self.command("plan"))
        self.assertFalse(self.data.exists())
        self.assertEqual(self.globals.read_text(), self.before)
        old = self.data / "icons/WitnessOpsIconsV13/keep.svg"
        old.parent.mkdir(parents=True)
        old.write_text("older set remains")
        self.assert_success(self.command("install", "--yes"))
        first = self.receipts()[0]
        state = json.loads(first.read_text())
        self.assertEqual(state["previous_theme"], "breeze-dark")
        self.assertIn("Size=24", state["configuration_backup"]["icons_section"])
        self.assertEqual(len(state["created_files"]), 3)
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        self.assert_success(self.command("verify"))
        self.assert_success(self.command("install", "--yes"))
        repeated = self.receipts()[-1]
        self.assertEqual(json.loads(repeated.read_text())["created_files"], [])
        self.assert_success(self.command("restore", "--backup", str(repeated)))
        self.assertTrue((self.target / "index.theme").is_file())
        extra = self.target / "my-added-icon.svg"
        extra.write_text("unrecorded operator addition")
        self.assert_success(self.command("restore", "--backup", str(first)))
        self.assertEqual(list(self.target.iterdir()), [extra])
        self.assertEqual(extra.read_text(), "unrecorded operator addition")
        self.assertEqual(old.read_text(), "older set remains")
        self.assertEqual(self.globals.read_text(), self.before)
        self.assert_success(self.command("restore", "--backup", str(first)))

    def test_conflicting_destination_is_refused_before_receipt_or_config_changes(self):
        self.target.mkdir(parents=True)
        keep = self.target / "index.theme"
        keep.write_text("existing conflicting file")
        result = self.command("install", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(keep.read_text(), "existing conflicting file")
        self.assertEqual(self.receipts(), [])
        self.assertEqual(self.globals.read_text(), self.before)

    def test_source_and_destination_symlinks_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.source / "scalable/apps/linked.svg").symlink_to(self.globals)
        self.assertNotEqual(self.command("install", "--yes").returncode, 0)
        self.assertFalse(self.data.exists())
        (self.source / "scalable/apps/linked.svg").unlink()
        self.data.mkdir()
        (self.data / "icons").symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.command("install", "--yes").returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        (self.data / "icons").unlink()
        self.globals.unlink()
        self.globals.symlink_to(self.source / "index.theme")
        self.assertNotEqual(self.command("install", "--yes").returncode, 0)
        self.assertFalse(self.target.exists())

    def test_edited_payload_refuses_entire_rollback(self):
        self.assert_success(self.command("install", "--yes"))
        receipt = self.receipts()[0]
        edited = self.target / "scalable/apps/chatgpt.svg"
        edited.write_text("operator's later edit")
        result = self.command("restore", "--backup", str(receipt))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(edited.read_text(), "operator's later edit")
        self.assertTrue((self.target / "scalable/apps/folder.svg").is_file())
        self.assertEqual(self.globals.read_text(), self.before)

    def fake_kde_environment(self):
        tools = self.root / "fake-bin"
        tools.mkdir()
        # This stand-in writes only the temporary fixture and records the
        # command. It never connects to the session bus or runs desktop tools.
        tool = tools / "kwriteconfig6"
        tool.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
assert args[args.index('--group') + 1] == 'Icons'
assert args[args.index('--key') + 1] == 'Theme'
assert '--notify' in args
path = pathlib.Path(args[args.index('--file') + 1])
lines = path.read_text().splitlines(keepends=True) if path.exists() else []
output, inside, wrote = [], False, False
value = args[-1]
delete = '--delete' in args
for line in lines:
    if line.startswith('['):
        if inside and not wrote and not delete:
            output.append('Theme=' + value + '\\n'); wrote = True
        inside = line.strip() == '[Icons]'
    if inside and line.startswith('Theme='):
        if not delete:
            output.append('Theme=' + value + '\\n'); wrote = True
    else:
        output.append(line)
if not wrote and not delete:
    if not inside:
        output.append('[Icons]\\n')
    output.append('Theme=' + value + '\\n')
path.write_text(''.join(output))
with open(os.environ['FIXTURE_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if os.environ.get('FIXTURE_FAIL_ON_NEW_THEME') and value == 'WitnessOpsIconsV1_0':
    sys.exit(23)
''')
        tool.chmod(0o755)
        return {**os.environ, "PATH": str(tools) + os.pathsep + os.environ.get("PATH", ""),
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent-icon-set-fixture",
                "DISPLAY": ":fixture", "FIXTURE_LOG": str(self.root / "commands.jsonl")}

    def test_activation_and_restore_touch_only_theme_key_with_fake_kde(self):
        env = self.fake_kde_environment()
        self.assert_success(self.command("install", "--yes", active=True, env=env))
        receipt = self.receipts()[0]
        self.assertEqual(self.globals.read_text(), self.before.replace("Theme=breeze-dark", "Theme=" + THEME))
        self.assert_success(self.command("verify", active=True, env=env))
        self.globals.write_text(self.globals.read_text().replace("font=Keep me", "font=Later unrelated edit"))
        self.assert_success(self.command("restore", "--backup", str(receipt), active=True, env=env))
        self.assertEqual(self.globals.read_text(), self.before.replace("font=Keep me", "font=Later unrelated edit"))
        self.assertFalse(self.target.exists())
        commands = [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(command[command.index("--file") + 1] == str(self.globals) for command in commands))

    def test_changed_selection_refused_and_absent_previous_key_restored(self):
        env = self.fake_kde_environment()
        self.globals.write_text(self.before.replace("Theme=breeze-dark\n", ""))
        self.assert_success(self.command("install", "--yes", active=True, env=env))
        receipt = self.receipts()[0]
        self.globals.write_text(self.globals.read_text().replace("Theme=" + THEME, "Theme=another-theme"))
        self.assertNotEqual(self.command("restore", "--backup", str(receipt), active=True, env=env).returncode, 0)
        self.assertIn("Theme=another-theme", self.globals.read_text())
        self.assertTrue((self.target / "index.theme").is_file())
        self.globals.write_text(self.globals.read_text().replace("Theme=another-theme", "Theme=" + THEME))
        self.assert_success(self.command("restore", "--backup", str(receipt), active=True, env=env))
        self.assertEqual(self.globals.read_text(), self.before.replace("Theme=breeze-dark\n", ""))
        commands = [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]
        self.assertIn("--delete", commands[-1])

    def test_failed_activation_restores_prior_selection_and_owned_files(self):
        env = self.fake_kde_environment()
        env["FIXTURE_FAIL_ON_NEW_THEME"] = "1"
        self.assertNotEqual(self.command("install", "--yes", active=True, env=env).returncode, 0)
        self.assertEqual(self.globals.read_text(), self.before)
        self.assertFalse(self.target.exists())
        self.assertEqual(json.loads(self.receipts()[0].read_text())["status"], "rolled_back")


if __name__ == "__main__":
    unittest.main()
