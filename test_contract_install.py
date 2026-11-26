from hashlib import sha256
import fcntl
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

NS = runpy.run_path(str(Path(__file__).with_name("install-contract-admin.py")))
install = NS["install"]
sha = lambda b: sha256(b).hexdigest()


class ContractInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in ["Plugin", "Game2", "state", "updates"]:
            (self.root / directory).mkdir()
        self.plugin = self.root / "Plugin" / NS["DLL"]
        self.plugin.write_bytes(b"stock")
        (self.root / "Game2/SpaceEngineers2.dll").write_bytes(b"engine")
        (self.root / "Game2/Game2.Simulation.dll").write_bytes(b"simulation")
        with ZipFile(self.root / "updates/Y51-SE2Online-0.18.5-AMP.zip", "w") as z:
            z.writestr(NS["DLL"], b"stock")
        for f in ["SE2-Linux.patch", "prepare-runtime.py"]:
            (self.root / f).write_bytes(b"fixture")
        def compile_binary(game, work):
            path = work / "result.dll"
            path.write_bytes(b"admin")
            return path
        overrides = dict(STOCK=sha(b"stock"), SIMULATION=sha(b"simulation"), PROJECT=self.root,
                         BUILDER={"ENGINE_HASH": sha(b"engine")}, compile_plugin=compile_binary)
        ctx = patch.dict(install.__globals__, overrides)
        ctx.start()
        self.addCleanup(ctx.stop)

    def test_install_restore_and_repeat_without_rebuilding(self):
        install(self.root)
        self.assertEqual(self.plugin.read_bytes(), b"admin")
        with patch.dict(install.__globals__, compile_plugin=None):
            install(self.root)
            install(self.root, True)
            self.assertEqual(self.plugin.read_bytes(), b"stock")
            install(self.root)
            self.assertEqual(self.plugin.read_bytes(), b"admin")

    def test_unknown_binary_and_active_game_are_preserved(self):
        with (self.root / "state/.amp.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "Stop the game"):
                install(self.root)
        self.plugin.write_bytes(b"custom")
        with self.assertRaisesRegex(ValueError, "Unrecognised"):
            install(self.root)
        self.assertEqual(self.plugin.read_bytes(), b"custom")

    def test_failed_build_and_mismatched_engine_are_preserved(self):
        def fail(*args):
            raise ValueError("build failed")
        with patch.dict(install.__globals__, compile_plugin=fail):
            with self.assertRaisesRegex(ValueError, "build failed"):
                install(self.root)
        (self.root / "Game2/Game2.Simulation.dll").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "supported"):
            install(self.root)
        self.assertEqual(self.plugin.read_bytes(), b"stock")
