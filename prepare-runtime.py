"""Build a private SpaceEngineers2 instance directory from the owner's Y51 downloads."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import Request, urlopen
import zipfile

PROJECT = Path(__file__).resolve().parent
SOURCE_HASH = "16566ffa06dd189b41b4835c48f1654b8ff4238b96923e28b4aac1bbb539b96d"
Y51_COMMIT = "8f2ccbe713f54ae894496ef34f2975dfa03a301f"
ENGINE_HASH = "1e20004a1f45ea650f667e34b09ae7446bbdc1dc53de3c6ff11bf8bb7f5c601a"
SDK = "mcr.microsoft.com/dotnet/sdk:10.0-noble@sha256:bef136a371963aaa46ec97882845ef6f54dec15728bb4b128b1c2a400ffbc9b4"
COMPAT_COMMIT = "f713880a05e1ffad2d6b489a6c1bc3c36103e030"
NATIVE = [
    ("linux-dependencies", "v1.0.42", "se2-dependencies.tar.gz",
     "c71e5c1af5e65af5072f8c436624ab4b095765e88416eaa1ddfc050fdd04a341", "libSDL3.so.0"),
    ("linux-native-wrappers", "v1.0.66", "se2-native-wrappers.tar.gz",
     "20e73eb8cccd1e6fc00e58dafd040228855b1b74c3929d071f107390e4efd90a", "libVRage.Physics.Native.so"),
]


def verify(path, expected):
    with path.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != expected:
            raise ValueError(f"Unexpected SHA-256 for {path.name}")


def extract_release(url, archive, expected, target):
    with urlopen(Request(url, headers={"User-Agent": "se2-ubuntu-amp"}), timeout=120) as src, archive.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    verify(archive, expected)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(target, filter="data")


def prepare_sources(source_zip, target):
    subprocess.run(["git", "init", "-q", str(target)], check=True)
    if source_zip is None:
        subprocess.run(["git", "-C", str(target), "fetch", "--depth=1",
                        "https://github.com/yellow5111/SE2Online.git", Y51_COMMIT], check=True)
        subprocess.run(["git", "-C", str(target), "checkout", "--detach", Y51_COMMIT], check=True)
    else:
        # Keep the original three-argument invocation for hosts rebuilding 0.18.4.
        verify(source_zip, SOURCE_HASH)
        with zipfile.ZipFile(source_zip) as z:
            z.extractall(target)
    patch = PROJECT / "SE2-Linux.patch"
    # Upstream ships CRLF. Normalize just the three patched files before git apply.
    for line in patch.read_text(encoding="utf-8").splitlines():
        if line.startswith("--- a/"):
            p = target / line[6:]
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
    apply = ["git", "-c", "core.autocrlf=false", "apply"]
    subprocess.run([*apply, "--check", str(patch)], cwd=target, check=True)
    subprocess.run([*apply, str(patch)], cwd=target, check=True)


def verify_standalone(package):
    runtime = package / "runtime"
    verify(runtime / "Game2/SpaceEngineers2.dll", ENGINE_HASH)
    manifest = json.loads((package / "runtime-manifest.json").read_text(encoding="utf-8-sig"))
    if manifest["EngineVersion"] != "4.0.0.3169":
        raise ValueError("Expected engine version 4.0.0.3169")
    for entry in manifest["Files"]:
        name = entry["Path"].replace("\\", "/")
        if name.startswith(("runtime/Game2/", "runtime/GameData/", "runtime/VRage/")):
            path = (package / name).resolve()
            if not path.is_relative_to(runtime) or path.stat().st_size != entry["Bytes"]:
                raise ValueError(f"Invalid standalone file: {name}")
    return runtime


def build(docker, src, game, project, property_name):
    subprocess.run([*docker, "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-e", "HOME=/tmp", "-e", "DOTNET_CLI_HOME=/tmp",
                    "-v", f"{src}:/src", "-v", f"{game}:/game:ro", "-w", "/src",
                    SDK, "dotnet", "build", project, "-c", "Release", f"-p:{property_name}=/game"], check=True)


def stage(source, standalone, dest, docker):
    if dest.exists() or dest.is_symlink():
        raise ValueError(f"Destination already exists: {dest}")
    if dest.is_relative_to(PROJECT) or dest.is_relative_to(standalone):
        raise ValueError("Choose a new destination outside the checkout and the standalone download.")
    runtime = verify_standalone(standalone)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="se2-build-", dir=dest.parent) as tmp:
        work = Path(tmp)
        out = work / "instance"
        out.mkdir()
        shutil.copy2(PROJECT / "updates/se2-panel-v4.pyz", out / "se2-panel-v4.pyz")
        y51 = work / "y51"
        prepare_sources(source, y51)
        build(docker, y51, runtime / "Game2", "Y51_SE2Online/Y51_SE2Online.csproj", "SE2Bin")
        plugin = y51 / "Y51_SE2Online/bin/Release"
        (out / "Plugin").mkdir()
        for name in ("Y51_SE2Online.dll", "Y51_SE2Online.deps.json", "0Harmony.dll"):
            shutil.copy2(plugin / name, out / "Plugin" / name)
        shutil.copytree(plugin / "Content", out / "Plugin/Content")
        shutil.copy2(y51 / "LICENSE", out / "Plugin/LICENSE")
        compat = work / "linux-compat2"
        subprocess.run(["git", "init", "-q", str(compat)], check=True)
        subprocess.run(["git", "-C", str(compat), "fetch", "--depth=1",
                        "https://github.com/CometWorks/linux-compat2.git", COMPAT_COMMIT], check=True)
        subprocess.run(["git", "-C", str(compat), "checkout", "--detach", COMPAT_COMMIT], check=True)
        build(docker, compat, runtime / "Game2", "ClientPlugin/ClientPlugin.csproj", "Game2")

        pulsar = out / "pulsar"
        asset = "pulsar-v2.4.3-linux-x64.tar.gz"
        req = Request("https://api.github.com/repos/SpaceGT/Pulsar/releases/tags/v2.4.3",
                      headers={"User-Agent": "se2-ubuntu-amp"})
        with urlopen(req, timeout=30) as response:
            release = json.load(response)
        entry = next(a for a in release["assets"] if a["name"] == asset)
        digest = entry.get("digest") or ""
        if not digest.startswith("sha256:"):
            raise ValueError("GitHub did not supply a SHA-256 digest for the Pulsar release.")
        extract_release(entry["browser_download_url"], work / asset, digest[7:], pulsar)
        (pulsar / "Modern.bin").chmod(0o755)
        mod = pulsar / "Modern/Local/linux-compat"
        mod.mkdir(parents=True, exist_ok=True)
        shutil.copy2(compat / "ClientPlugin/bin/Release/net10.0/LinuxCompat.dll", mod / "plugin.dll")
        shutil.copy2(compat / "ClientPlugin/ClientPlugin.xml", mod / "plugin.xml")
        shutil.copy2(compat / "LICENSE", mod / "LICENSE")

        libs = out / "native/lib"
        libs.mkdir(parents=True)
        for repo, tag, name, sha, sentinel in NATIVE:
            extracted = work / repo
            extract_release(f"https://github.com/CometWorks/{repo}/releases/download/{tag}/{name}",
                            work / name, sha, extracted)
            matches = list(extracted.rglob(sentinel))
            if len(matches) != 1:
                raise ValueError(f"Expected one {sentinel} in {name}")
            shutil.copytree(matches[0].parent, libs, dirs_exist_ok=True, symlinks=True)
        for name in ("Game2", "GameData", "VRage"):
            shutil.copytree(runtime / name, out / name)
        shutil.copy2(runtime / "SE2-Licenses.txt", out / "SE2-Licenses.txt")
        subprocess.run([*docker, "run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--entrypoint", "/usr/bin/python3", "-v", f"{out}:/instance:ro", "-w", "/instance",
                        "local/se2-y51-amp:0.18.4-r2", "-B", "/instance/se2-panel-v4.pyz", "--check"], check=True)
        out.rename(dest)
    print(f"Prepared {dest}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_zip", type=Path, nargs="?",
                        help="Legacy build only: supply the original 0.18.4 source ZIP to build 0.18.4")
    parser.add_argument("standalone", type=Path, help="Extracted standalone.1 directory containing runtime/")
    parser.add_argument("destination", type=Path, help="New private instance directory outside this repository")
    parser.add_argument("--sudo-docker", action="store_true", help="Run only the Docker commands using sudo")
    args = parser.parse_args()
    stage(args.source_zip.resolve() if args.source_zip else None, args.standalone.resolve(), args.destination.resolve(),
          ["sudo", "docker"] if args.sudo_docker else ["docker"])
