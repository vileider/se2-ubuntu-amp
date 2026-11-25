import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("panel_update", ROOT / "enable-panel-update.py")
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
STAGES = json.loads((ROOT / "y51-se2updates.json").read_bytes())
ORIGINAL = b"A=1\r\nApp.UpdateSources=[]\r\nB=2\r\n"


class PanelUpdateTests(unittest.TestCase):
    def test_patch_and_backup_are_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "GenericModule.kvp"
            cfg.write_bytes(ORIGINAL)
            panel.install(cfg, STAGES)
            panel.install(cfg, STAGES)
            data = cfg.read_bytes()
            self.assertEqual(json.loads(panel.setting(data)[1]), STAGES)
            self.assertEqual(panel.SETTING.sub(b"App.UpdateSources=[]", data), ORIGINAL)
            self.assertEqual([p.read_bytes() for p in cfg.parent.glob("*.before-y51-*")], [ORIGINAL])

    def test_rejects_custom_and_duplicate_settings(self):
        for data in (ORIGINAL.replace(b"[]", b'[{"UpdateSource":"Other"}]'), ORIGINAL * 2):
            with self.subTest(data=data), self.assertRaises(ValueError):
                panel.needs_change(data, STAGES)

    def test_updates_between_stop_and_start_and_checks_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            inst = home / ".ampdata/instances/Test01"
            game = inst / "SpaceEngineers2"
            (game / "Game2").mkdir(parents=True)
            (game / "Game2/SpaceEngineers2.dll").touch()
            (game / "state").mkdir()
            cfg = inst / "GenericModule.kvp"
            cfg.write_bytes(ORIGINAL)

            def manager(cmd, check):
                if cmd[1] == "-q":
                    self.assertEqual(cfg.read_bytes(), ORIGINAL)
                else:
                    self.assertFalse(panel.needs_change(cfg.read_bytes(), STAGES))

            with (
                patch.object(Path, "home", return_value=home),
                patch.object(panel.os, "geteuid", return_value=cfg.stat().st_uid, create=True),
                patch.dict(panel.sys.modules, {"fcntl": Mock(LOCK_EX=2, LOCK_NB=4)}),
                patch.object(panel, "running", side_effect=[True, False]),
                patch.object(panel.subprocess, "run", side_effect=manager) as commands,
                patch.object(panel.subprocess, "check_output", side_effect=lambda cmd: cfg.read_bytes()) as read,
            ):
                panel.main("Test01")
            self.assertEqual([call.args[0][1] for call in commands.call_args_list], ["-q", "-s"])
            self.assertEqual(read.call_args.args[0][:3], ["docker", "exec", "AMP_Test01"])
