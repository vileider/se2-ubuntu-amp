import contextlib
import importlib.util
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import unittest

PACKAGE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("service", PACKAGE / "service.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class TemplateTests(unittest.TestCase):
    def test_ads_deployment_metadata(self):
        folder = PACKAGE
        source = folder / "y51-se2.kvp"
        pairs = [line.split("=", 1) for line in source.read_text(encoding="utf-8").splitlines() if line]
        settings = dict(pairs)
        self.assertEqual(len(settings), len(pairs), "Duplicate template key")
        self.assertRegex(settings["Meta.MinAMPVersion"], r"^\d+\.\d+\.\d+\.\d+$")
        self.assertGreater(int(settings["Meta.ConfigVersion"]), 0)
        self.assertEqual(settings["Meta.ConfigRoot"], source.name)
        for key in ("Meta.ConfigManifest", "Meta.MetaConfigManifest"):
            self.assertIsInstance(service.read_json(folder / settings[key]), list)

    def test_panel_defaults_and_existing_world(self):
        template = PACKAGE
        kvp = dict(line.split("=", 1) for line in (template / "y51-se2.kvp").read_text().splitlines())
        fields = {entry["FieldName"]: entry["DefaultValue"]
                  for entry in service.read_json(template / kvp["Meta.ConfigManifest"])}
        defaults = {k: fields[v[2:-2]] for k, v in json.loads(kvp["App.EnvironmentVariables"]).items()}
        ports = [p["Port"] for p in json.loads(kvp["App.Ports"])]
        self.assertEqual(ports, [27016, 27017])
        self.assertEqual(kvp["App.ExitString"], "stop now")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario = root / "GameData/Vanilla/Worlds/EmptyWorld"
            scenario.mkdir(parents=True)
            (scenario / "savegame.vrb").touch()
            config = service.configure(root, *ports, dict(defaults, SE2_PASSWORD="private pass"))
            self.assertEqual(defaults["SE2_STREAMING_RADIUS_METERS"], "")
            self.assertNotIn("StreamingRadiusMeters", service.read_json(config)["Network"])
            for value in ("1000", "1000000", "0", "5000"):
                service.configure(root, *ports, dict(defaults, SE2_STREAMING_RADIUS_METERS=value))
                self.assertEqual(service.read_json(config)["Network"]["StreamingRadiusMeters"], int(value))
            before = config.read_bytes()
            for invalid in ("999", "1000001", "-1", "5km"):
                with self.assertRaises(ValueError):
                    service.configure(root, *ports, dict(defaults, SE2_STREAMING_RADIUS_METERS=invalid))
                self.assertEqual(config.read_bytes(), before)
            state = {"SaveId": "SE2 World 1", "WorldName": "SE2 World", "SourceContent": "EmptyWorld"}
            (config.parent / "dedicated-state.json").write_text(json.dumps(state))
            service.configure(root, *ports, defaults)
            net = service.read_json(config)["Network"]
            self.assertEqual(net["StreamingRadiusMeters"], 5000)
            self.assertEqual((net["SessionName"], net["ListenPort"], net["Password"], net["MaxPlayers"]),
                             ("SE2 Online Server", 27016, "private pass", 8))
            for args in [(27016, 27018, defaults), (*ports, dict(defaults, SE2_WORLD_NAME="Other"))]:
                with self.assertRaises(ValueError):
                    service.configure(root, *args)

    def test_control_channel_and_save_ack(self):
        simulation = """
import json, os, pathlib, sys, time
d=pathlib.Path(sys.argv[1])
cmd=d/'dedicated-command.txt'
s={'ProcessId':os.getpid(), 'WorldLoaded':True, 'Status':'Hosting test', 'NetworkSteamIdentity':'42',
   'Players':[{'SteamId':'71', 'Name':'Test Player', 'State':'Playing'}]}
def publish():
    tmp=d/'status.tmp'
    tmp.write_text(json.dumps(s))
    tmp.replace(d/'dedicated-status.json')
publish()
for expected in ('save', 'stop now'):
    deadline=time.monotonic()+8
    while not cmd.exists() and time.monotonic()<deadline: time.sleep(.025)
    assert cmd.read_text().strip()==expected
    cmd.unlink()
    time.sleep(.35)
if sys.argv[2]=='saved':
    s.update(Status='Shutdown complete', FinalSaveCompleted=True, LastSaveUtc='test', Players=[])
    publish()
sys.exit(137)
"""
        for final, expected in [("saved", 0), ("unsaved", 137)]:
            with self.subTest(final=final), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                commands = queue.Queue()
                commands.put("save")
                commands.put("stop now")
                child = subprocess.Popen([sys.executable, "-c", simulation, tmp, final])
                with child, contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(service.supervise(child, root, commands, 0), expected)
                    self.assertIn("SE2_HOSTING_READY", stdout.getvalue())
                    self.assertIn("SE2_PLAYER_JOIN 71 Test Player", stdout.getvalue())

    def test_existing_command_and_stale_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "dedicated-command.txt"
            self.assertTrue(service.publish_command(path, "save"))
            self.assertFalse(service.publish_command(path, "stop now"))
            self.assertEqual(path.read_text(), "save\n")
            stale = {"ProcessId": -1, "Status": "Hosting old", "NetworkSteamIdentity": "123", "WorldLoaded": True}
            (root / "dedicated-status.json").write_text(json.dumps(stale))
            with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    service.supervise(child, root, queue.Queue(), 0)
                    self.assertNotIn("SE2_HOSTING_READY", out.getvalue())


if __name__ == "__main__":
    unittest.main()
