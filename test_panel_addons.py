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
    def test_panel_json_and_published_launcher(self):
        cfg = json.loads((ROOT / "y51-se2config.json").read_bytes())
        self.assertEqual(next(row["Special"] for row in cfg if row["FieldName"] == "PanelAddons"),
                         f"listfile:./SpaceEngineers2/{addons.LIST}")
        with zipfile.ZipFile(ROOT / "updates/se2-panel-v2.pyz") as z:
            for name, source in [("__main__.py", "service.py"), ("panel_addons.py", "panel_addons.py")]:
                self.assertEqual(z.read(name), (ROOT / source).read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            output = subprocess.check_output([sys.executable, "-I", str(ROOT / "updates/se2-panel-v2.pyz"),
                                              "--check-addons", tmp], text=True)
            self.assertIn("Sprawdzono listę (0)", output)

    def test_amp_start_validates_pinned_artifact_and_update_only_handles_y51(self):
        kvp = (ROOT / "y51-se2.kvp").read_text()
        self.assertIn("App.PreStartStages=@IncludeJson[y51-se2start.json]", kvp)
        self.assertIn("App.CommandLineArgs=-I -u ./se2-panel-v2.pyz ", kvp)
        stages = json.loads((ROOT / "y51-se2start.json").read_bytes())
        self.assertRegex(stages[0]["UpdateSourceData"], r"/[0-9a-f]{40}/updates/se2-panel-v2[.]pyz$")
        update = json.loads((ROOT / "y51-se2updates.json").read_bytes())
        self.assertEqual([item["UpdateSource"] for item in update], ["FetchURL", "Executable"])
        with tempfile.TemporaryDirectory(prefix="amp path ") as tmp:
            name = stages[0]["UpdateSourceArgs"]
            deployed = Path(tmp) / name
            shutil.copyfile(ROOT / "updates" / name, deployed)
            args = shlex.split(stages[1]["UpdateSourceArgs"].replace("{{$FullBaseDir}}", tmp+"/"))
            subprocess.run([sys.executable, *args], check=True)
            deployed.write_bytes(b"incomplete")
            result = subprocess.run([sys.executable, *args], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Invalid", result.stderr)

    def test_lookup_and_restart_offline_preserve_unmanaged_content(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            root = Path(tmp)
            selection = root / addons.LIST
            selection.write_text("11\nhttps://steamcommunity.com/sharedfiles/filedetails/?id=11\nhttps://github.com/Example/Addon\n")
            with patch.object(addons, "fetch", side_effect=[workshop(), registry()]) as fetch:
                rows = addons.check(root)
                self.assertEqual(addons.check(root), rows)
                self.assertEqual(fetch.call_count, 2)
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
                with patch.object(addons, "fetch", return_value=data), self.assertRaises(ValueError):
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
            for profile in (None, root / "custom profile.xml"):
                service.launch(root, root / "server.json", profile)
                args = popen.call_args.args[0]
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
