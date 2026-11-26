import codecs
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import repair_instance_template as m


class TemplateRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "MySE201"
        self.game = self.root / m.GAME
        (self.game / "Game2").mkdir(parents=True)
        (self.game / "Game2/SpaceEngineers2.dll").touch()
        (self.game / "state").mkdir()
        self.kvp = self.root / "GenericModule.kvp"
        self.env = {"SE2_SESSION_NAME": "local override", "MY_LOCAL_FLAG": "yes"}
        self.old = codecs.BOM_UTF8 + ("\r\n".join((
            "Meta.ConfigVersion=2", "Meta.ConfigRoot=y51-se2.kvp",
            "Meta.OriginalSource=vileider-se2-ubuntu-amp-main",
            "App.BaseDirectory=./SpaceEngineers2/", "App.ExecutableLinux=/usr/bin/python3",
            "App.CommandLineArgs=-u /opt/se2-amp/service.py {{$ApplicationPort1}} {{$ApplicationPort2}}",
            'App.AppSettings={"SessionName":"knt se2 ubuntu","WorldName":"Verdure november"}',
            'App.Ports=[{"Protocol":"UDP","Port":27016}]',
            "App.EnvironmentVariables=" + json.dumps(self.env),
            "App.PreStartStages=[]", 'App.UpdateSources=[{"UpdateStageName":"local updater"}]',
        )) + "\r\n").encode()
        self.kvp.write_bytes(self.old)
        self.manifest = self.root / "configmanifest.json"
        self.manifest.write_text('[{"FieldName":"LocalSetting","DisplayName":"local"}]')

    def test_repair_v2_preserves_values_and_is_idempotent(self):
        m.install(self.root, m.plan(self.root))
        data = self.kvp.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        new = m.kvp(data.decode("utf-8-sig"))
        old = m.kvp(self.old.decode("utf-8-sig"))
        for unchanged in ("App.AppSettings", "App.Ports", "Meta.OriginalSource", "App.UpdateSources"):
            self.assertEqual(new[unchanged], old[unchanged])
        env = json.loads(new["App.EnvironmentVariables"])
        self.assertEqual({k: env[k] for k in self.env}, self.env)
        self.assertEqual(env["SE2_STREAMING_RADIUS_METERS"], "{{StreamingRadiusMeters}}")
        self.assertEqual(new["Meta.ConfigVersion"], "4")
        self.assertIn(m.LAUNCHER, new["App.CommandLineArgs"])
        fields = {r["FieldName"] for r in json.loads(self.manifest.read_bytes())}
        self.assertTrue({"WorldSaveRequest", "PanelAddons", "LocalSetting"} <= fields)
        self.assertEqual(json.loads((self.root / "metaconfig.json").read_bytes())[0]["ConfigFile"],
                         "amp-world-save.kvp")
        self.assertEqual((self.game / m.LAUNCHER).read_bytes(), (m.SOURCE / "updates" / m.LAUNCHER).read_bytes())
        self.assertEqual(m.plan(self.root), {})
        m.install(self.root, m.plan(self.root))
        backup, = self.root.glob(".before-se2-panel-v4-*")
        self.assertEqual((backup / "GenericModule.kvp").read_bytes(), self.old)

    def test_partial_write_rolls_back_existing_and_new_files(self):
        changes = m.plan(self.root)
        original = Path.replace

        def fail_on_launcher(path, target):
            if target == self.game / m.LAUNCHER:
                raise OSError("simulated interrupted write")
            return original(path, target)

        with patch.object(Path, "replace", fail_on_launcher), self.assertRaises(OSError):
            m.install(self.root, changes)
        self.assertEqual(self.kvp.read_bytes(), self.old)
        self.assertEqual(json.loads(self.manifest.read_bytes()), [{"FieldName": "LocalSetting", "DisplayName": "local"}])
        self.assertFalse((self.root / "metaconfig.json").exists())
        self.assertFalse((self.game / m.LAUNCHER).exists())

    def test_live_game_blocks_before_stopping_panel(self):
        with (
            (self.game / "state/.amp.lock").open("a") as lock,
            patch.object(m, "running", return_value=True),
            patch.object(m.subprocess, "run") as run,
        ):
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "Stop the game"):
                m.repair(self.root.name, self.root.parent)
            run.assert_not_called()
        self.assertEqual(self.kvp.read_bytes(), self.old)

    def test_restore_running_panel_and_verify_mount_after_reloading(self):
        with (
            patch.object(m, "running", side_effect=[True, False, True]),
            patch.object(m.subprocess, "run") as run,
            patch.object(m.subprocess, "check_output", return_value=str(self.root)),
        ):
            m.repair(self.root.name, self.root.parent)
        self.assertEqual([args[0][0] for args in run.call_args_list],
                         [["ampinstmgr", "-q", self.root.name], ["ampinstmgr", "-s", self.root.name]])

    def test_offline_panel_stays_offline(self):
        with patch.object(m, "running", return_value=False), patch.object(m.subprocess, "run") as run:
            m.repair(self.root.name, self.root.parent)
            run.assert_not_called()

    def test_v3_prestart_upgrade_preserves_unrelated_steps(self):
        old_stages = [{"UpdateStageName": "before"}, {"UpdateStageName": "Prepare the AMP add-on manager"},
                      {"UpdateStageName": "Verify the AMP add-on manager"}, {"UpdateStageName": "after"}]
        self.kvp.write_bytes(self.old.replace(b"App.PreStartStages=[]",
            ("App.PreStartStages=" + json.dumps(old_stages)).encode()))
        m.install(self.root, m.plan(self.root))
        kvp = m.kvp(self.kvp.read_text(encoding="utf-8-sig"))
        steps = json.loads(kvp["App.PreStartStages"])
        self.assertEqual(steps[1:3], json.loads((m.SOURCE / "y51-se2start.json").read_bytes()))
        self.assertEqual([steps[0], steps[-1]], [old_stages[0], old_stages[-1]])
