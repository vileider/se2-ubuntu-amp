"""Build the optional server-console contract commands against this host's SE2 runtime."""
import argparse
import fcntl
from hashlib import sha256
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parent
BUILDER = runpy.run_path(str(PROJECT / "prepare-runtime.py"))
SOURCE = PROJECT / "contract-admin/ContractAdmin.cs"
PATCH = PROJECT / "contract-admin/command.patch"
DLL = "Y51_SE2Online.dll"
STOCK = "743c11fe1695815ca8619e6430876ebf9d4574aba8eca97b63a9f2447b8ef69b"
SIMULATION = "0db271c7c941a19cb1f5999363c1a7c18b13d30ae3aecb876f1423520a33fd09"


def digest(file):
    return sha256(file.read_bytes()).hexdigest()


def add_commands(checkout):
    folder = checkout / "Y51_SE2Online/Y51_SE2Online/Dedicated"
    runtime = folder / "DedicatedRuntime.cs"
    runtime.write_bytes(runtime.read_bytes().replace(b"\r\n", b"\n"))
    subprocess.run(["git", "-c", "core.autocrlf=false", "apply", str(PATCH)], cwd=checkout, check=True)
    shutil.copyfile(SOURCE, folder / SOURCE.name)


def compile_plugin(game, work):
    src = work / "source"
    BUILDER["prepare_sources"](None, src)
    add_commands(src)
    BUILDER["build"](["docker"], src, game, "Y51_SE2Online/Y51_SE2Online.csproj", "SE2Bin")
    result = src / "Y51_SE2Online/bin/Release" / DLL
    if not result.is_file() or "ContractAdmin".encode() not in result.read_bytes():
        raise ValueError("The build did not produce the contract commands")
    return result


def install(root, restore=False):
    root = root.resolve()
    game = root / "Game2"
    plugin = root / "Plugin" / DLL
    if digest(game / "SpaceEngineers2.dll") != BUILDER["ENGINE_HASH"] or digest(game / "Game2.Simulation.dll") != SIMULATION:
        raise ValueError("This build requires the supported SE2 4.0.0.3169 assemblies")
    with (root / "state/.amp.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Stop the game in AMP, wait for the final save, then retry") from None
        cache = plugin.parent / ".contract-admin"
        current = digest(plugin)
        inputs = [SOURCE, PATCH, PROJECT / "SE2-Linux.patch", PROJECT / "prepare-runtime.py"]
        revision = sha256(b"".join(p.read_bytes() for p in inputs)).hexdigest()
        cached = cache / (revision + ".dll")
        if restore and current == STOCK:
            return "Stock Y51 is already installed"
        if not restore and cached.is_file() and current == digest(cached):
            return "SE2_CONTRACT_ADMIN_OK (already installed)"
        if current != STOCK and not any(current == digest(p) for p in cache.glob("*.dll")):
            raise ValueError("Unrecognised installed plugin; no files changed")
        cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="build-", dir=cache) as temp:
            work = Path(temp)
            staged = work / DLL
            if restore:
                with zipfile.ZipFile(PROJECT / "updates/Y51-SE2Online-0.18.5-AMP.zip") as z:
                    staged.write_bytes(z.read(DLL))
                if digest(staged) != STOCK:
                    raise ValueError("Stock DLL checksum mismatch")
            else:
                if not cached.exists():
                    binary = compile_plugin(game, work)
                    shutil.copyfile(binary, staged)
                    os.replace(staged, cached)
                shutil.copyfile(cached, staged)
            shutil.copymode(plugin, staged)
            os.replace(staged, plugin)
        return "Stock Y51 restored" if restore else "SE2_CONTRACT_ADMIN_OK"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", help="AMP instance name")
    parser.add_argument("--restore", action="store_true", help="Restore the stock AMP 0.18.5 DLL")
    args = parser.parse_args()
    if Path(args.instance).name != args.instance or args.instance in ("", ".", ".."):
        parser.error("Expected a single AMP instance name")
    try:
        root = Path.home() / ".ampdata/instances" / args.instance / "SpaceEngineers2"
        print(install(root, args.restore))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"SE2 contract admin: {exc}\n")
