from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import update_plugin as updater


class UpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.plugin = self.root / "Plugin" / updater.DLL
        self.plugin.parent.mkdir()
        self.plugin.write_bytes(b"previous")
        engine = self.root / "Game2/SpaceEngineers2.dll"
        engine.parent.mkdir()
        engine.write_bytes(b"test engine")
        releases = Path(__file__).resolve().parent / "updates"
        self.release = json.loads((releases / updater.CHANNEL).read_bytes())
        self.release["engine_sha256"] = sha256(engine.read_bytes()).hexdigest()
        package = releases / f"Y51-SE2Online-{self.release['version']}-AMP.zip"
        self.download = Mock(return_value=package.read_bytes())

    def test_install_and_repeat_preserves_backup(self):
        updater.update(self.root, self.release, self.download)
        self.assertEqual(sha256(self.plugin.read_bytes()).hexdigest(), self.release["dll_sha256"])
        updater.update(self.root, self.release, self.download)
        self.download.assert_called_once()
        self.assertEqual(self.plugin.with_suffix(".dll.previous").read_bytes(), b"previous")

    def test_invalid_release_keeps_installed_plugin(self):
        for key in ("engine_sha256", "zip_sha256", "dll_sha256"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                updater.update(self.root, dict(self.release, **{key: "incorrect"}), self.download)
            self.assertEqual(self.plugin.read_bytes(), b"previous")
            self.assertFalse(self.plugin.with_suffix(".dll.previous").exists())
