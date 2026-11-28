import contextlib
import io
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

import panel_addons as addons
import service


ROOT = Path(__file__).resolve().parent
HUB_XML = b'<PluginData xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="GitHubPlugin"><Id>test</Id><FriendlyName>Test plugin</FriendlyName><RepoId>Example/Addon</RepoId></PluginData>'


def workshop(item="11", app="1133870", tag="UGCModTypeTag", result=1):
    return json.dumps({"response": {"publishedfiledetails": [{
        "publishedfileid": item, "consumer_app_id": app, "title": "Test mod", "result": result, "tags": [{"tag": tag}]}]}}).encode()


def registry(xml=HUB_XML):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as z:
        z.writestr("PluginHub-main/Plugins/Test.xml", xml)
    return raw.getvalue()


class AddonTests(unittest.TestCase):
    def test_manifest_install_update_rollback_and_offline_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos = ["first/new-mod", "second/another-mod"]
            (root / addons.LIST).write_text("\n".join("https://github.com/" + repo for repo in repos))
            def manifest(data, version):
                return json.dumps({"schema": 1, "name": "Demo", "version": version,
                                   "dll": f"dist/{version}/Shared.dll",
                                   "sha256": addons.hashlib.sha256(data).hexdigest()}).encode()
            payloads = [b"mod A", b"mod B"]
            answers = [manifest(data, "1") for data in payloads] + payloads
            with patch.object(addons, "fetch", side_effect=answers):
                rows = addons.check(root)
            self.assertIsNone(addons.pulsar_profile(root, rows))
            with patch.object(addons, "fetch", side_effect=AssertionError("network on restart")):
                paths = addons.native_plugins(root, addons.check(root))
                self.assertEqual([p.read_bytes() for p in paths], payloads)
            cache = root / "state/amp-addons-cache.json"
            before = cache.read_bytes()
            with patch.object(addons, "fetch", side_effect=[manifest(b"new", "2"), manifest(b"B2", "2"), b"new", b"bad"]):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    addons.check(root, force=True)
            self.assertEqual(cache.read_bytes(), before)
            with patch.object(addons, "fetch", side_effect=[manifest(b"new", "2"), manifest(b"B2", "2"), b"B2"]):
                upgraded = addons.check(root, force=True)
            self.assertEqual([p.read_bytes() for p in addons.native_plugins(root, upgraded)], [b"new", b"B2"])
            self.assertEqual([p.read_bytes() for p in paths], payloads)
            (root / "home/.steam/sdk64").mkdir(parents=True)
            with patch.object(service.subprocess, "Popen") as popen:
                service.launch(root, root / "server.json", None, paths)
                expected = ";".join(map(str, [root / "Plugin/Y51_SE2Online.dll", *paths]))
                self.assertIn("-plugins:" + expected, popen.call_args.args[0])

    def test_v5_verdure_pin_migrates_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"old installed library"
            (root / addons.LIST).write_text("https://github.com/" + addons.VERDURE_REPO)
            old = root / "state/amp-plugins" / addons.VERDURE_DLL
            old.parent.mkdir(parents=True)
            old.write_bytes(data)
            row = {"source": "github", "key": addons.VERDURE_REPO, "id": addons.VERDURE_DLL, "name": "Verdure"}
            (root / "state/amp-addons-cache.json").write_text(json.dumps([row]))
            with patch.object(addons, "fetch", side_effect=AssertionError("network on migration")), \
                 patch.object(addons, "VERDURE_SHA256", addons.hashlib.sha256(data).hexdigest()):
                [installed] = addons.native_plugins(root, addons.check(root))
                self.assertEqual(installed.read_bytes(), data)

    def test_invalid_manifest_never_falls_back_to_pulsar(self):
        entry = {"schema": 1, "name": "Demo", "version": "1", "dll": "mod.dll", "sha256": "0"*64}
        for change in ({"schema": True}, {"version": 3}, {"sha256": ""}, {"dll": "../x.dll"},
                       {"dll": "https://elsewhere/x.dll"}, {"dll": "x;other.dll"}, {"dll": "do.sh"}):
            with self.subTest(change=change), patch.object(addons, "fetch", return_value=json.dumps({**entry, **change})), \
                 patch.object(addons, "hub_plugins", side_effect=AssertionError), self.assertRaises(ValueError):
                addons.resolve([("github", "third/mod")])

    def test_panel_json_and_published_launcher(self):
        cfg = json.loads((ROOT / "y51-se2config.json").read_bytes())
        self.assertEqual({r["Category"] for r in cfg}, {"Space Engineers 2:rocket_launch"})
        self.assertEqual({r["Subcategory"] for r in cfg}, {"Serwer:dns:1", "Świat:public:2", "Dodatki:extension:3"})
        self.assertEqual(next(row["Special"] for row in cfg if row["FieldName"] == "PanelAddons"),
                         f"listfile:./SpaceEngineers2/{addons.LIST}")
        with zipfile.ZipFile(ROOT / "updates/se2-panel-v4.pyz") as z:
            for name, source in [("__main__.py", "service.py"), ("panel_addons.py", "panel_addons.py")]:
                self.assertEqual(z.read(name), (ROOT / source).read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            output = subprocess.check_output([sys.executable, "-I", "-X", "utf8", str(ROOT / "updates/se2-panel-v4.pyz"),
                                              "--check-addons", tmp], encoding="utf-8")
            self.assertIn("Sprawdzono listę (0)", output)

    def test_start_and_cold_update_validate_and_run_bundle(self):
        kvp = (ROOT / "y51-se2.kvp").read_text()
        self.assertIn("App.PreStartStages=@IncludeJson[y51-se2start.json]", kvp)
        self.assertIn("App.CommandLineArgs=-I -u ./se2-panel-v4.pyz ", kvp)
        stages = json.loads((ROOT / "y51-se2start.json").read_bytes())
        self.assertRegex(stages[0]["UpdateSourceData"], r"/[0-9a-f]{40}/updates/se2-panel-v4[.]pyz$")
        update = json.loads((ROOT / "y51-se2updates.json").read_bytes())
        self.assertEqual(update[2], {**stages[0], "OverwriteExistingFiles": True})
        self.assertEqual(update[3], stages[1])
        self.assertTrue(all(not s["SkipOnFailure"] for s in update))
        with tempfile.TemporaryDirectory(prefix="amp path ") as tmp:
            name = stages[0]["UpdateSourceArgs"]
            deployed = Path(tmp) / name
            shutil.copyfile(ROOT / "updates" / name, deployed)
            args = shlex.split(stages[1]["UpdateSourceArgs"].replace("{{$FullBaseDir}}", tmp+"/"))
            subprocess.run([sys.executable, *args], check=True)
            args = shlex.split(update[-1]["UpdateSourceArgs"].replace("{{$FullBaseDir}}", tmp+"/"))
            subprocess.run([sys.executable, *args], check=True)
            args = shlex.split(stages[1]["UpdateSourceArgs"].replace("{{$FullBaseDir}}", tmp+"/"))
            deployed.write_bytes(b"incomplete")
            result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid", result.stderr)

    def test_lookup_and_restart_offline_preserve_unmanaged_content(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(tmp)
            selection = root / addons.LIST
            selection.write_text("11\nhttps://steamcommunity.com/sharedfiles/filedetails/?id=11\nhttps://github.com/Example/Addon\n")
            with patch.object(addons, "fetch", side_effect=[workshop(), addons.MissingFile(), registry()]) as fetch:
                rows = addons.check(root)
                self.assertEqual(addons.check(root), rows)
                self.assertEqual(fetch.call_count, 3)
            self.assertIn("Test mod", out.getvalue())
            self.assertIn("Test plugin", out.getvalue())
            profile = root / "pulsar/Modern/Profiles/Current.xml"
            profile.parent.mkdir(parents=True)
            original = b"<Profile><Name>Current</Name><GitHub/><DevFolder/><Local><string>mine</string></Local><Mods/></Profile>"
            profile.write_bytes(original)
            actual = ET.parse(addons.pulsar_profile(root, rows)).getroot()
            self.assertEqual(actual.findtext("GitHub/GitHubPluginConfig/Id"), "test")
            self.assertEqual(actual.findtext("Local/string"), "mine")
            self.assertEqual(profile.read_bytes(), original)
            cfg = {"Mods": [77]}
            owned = addons.apply_mods(cfg, rows, [])
            self.assertEqual((owned, cfg["Mods"]), (["11"], [77,"11"]))
            selection.write_text("")
            rows = addons.check(root)
            self.assertIsNone(addons.pulsar_profile(root, rows))
            self.assertEqual(addons.apply_mods(cfg, rows, owned), [])
            self.assertEqual(cfg["Mods"], [77])

    def test_invalid_lookup_never_replaces_last_valid_plan(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            listing = root / addons.LIST
            listing.write_text("11")
            with patch.object(addons, "fetch", return_value=workshop()):
                addons.check(root)
            cache = root / "state/amp-addons-cache.json"
            previous = cache.read_bytes()
            listing.write_text("12")
            for answer in [workshop("12", app="244850"), workshop("12", tag="UGCBlueprintTypeTag"),
                           workshop("12", result=9)]:
                with self.subTest(answer=answer), patch.object(addons, "fetch", return_value=answer), self.assertRaises(ValueError):
                    addons.check(root)
                self.assertEqual(cache.read_bytes(), previous)
            for url in ["https://example.com/download", "https://github.com/Example/Addon/releases", "0"]:
                with self.assertRaises(ValueError):
                    addons.source(url)
            listing.write_text("https://github.com/Example/Addon")
            for data in [registry(HUB_XML.replace(b"</PluginData>", b"<Platforms>Windows</Platforms></PluginData>")),
                         registry(HUB_XML.replace(b"Example/Addon", b"Other/Addon"))]:
                with patch.object(addons, "fetch", side_effect=[addons.MissingFile(), data]), self.assertRaises(ValueError):
                    addons.check(root)
            self.assertEqual(cache.read_bytes(), previous)

    def test_preexisting_mod_not_owned_by_panel(self):
        cfg = {"Mods": ["https://steamcommunity.com/sharedfiles/filedetails/?id=11", 22]}
        old = cfg["Mods"].copy()
        self.assertEqual(addons.apply_mods(cfg, [{"id":"11","source":"steam"}], []), [])
        addons.apply_mods(cfg, [], [])
        self.assertEqual(cfg["Mods"], old)

    def test_launcher_passes_profile_only_when_requested(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(service.subprocess, "Popen") as popen:
            root = Path(tmp)
            (root / "home/.steam/sdk64").mkdir(parents=True)
            for profile in (None, root / "custom profile.xml"):
                dll = root / "state/amp-plugins/VerdurePlanets.dll"
                service.launch(root, root / "server.json", profile, [dll] if profile else [])
                args = popen.call_args.args[0]
                libraries = [arg for arg in args if str(arg).startswith("-plugins:")]
                expected = "-plugins:" + str(root / "Plugin/Y51_SE2Online.dll")
                self.assertEqual(libraries, [expected + (";" + str(dll) if profile else "")])
                self.assertIn("-sources", args)
                self.assertEqual("-profile" in args, profile is not None)
                if profile:
                    self.assertEqual(args[args.index("-profile")+1], str(profile))

    def test_live_edit_reports_lookup_without_writing_server_config(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(tmp)
            (root / addons.LIST).write_text("11")
            with patch.object(addons, "fetch", return_value=workshop()), patch.object(addons.time, "sleep", side_effect=[None, KeyboardInterrupt]):
                with self.assertRaises(KeyboardInterrupt):
                    addons.live_preview(root, b"")
            self.assertIn("Test mod", out.getvalue())
            self.assertFalse((root / "state/server.json").exists())
