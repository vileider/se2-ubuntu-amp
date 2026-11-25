"""Install the public AMP plugin update for an already prepared SE2 runtime."""
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile


UPDATES = "https://raw.githubusercontent.com/vileider/se2-ubuntu-amp/main/updates"
CHANNEL = "stable-4.0.0.3169.json"
DLL = "Y51_SE2Online.dll"


def fetch(url, limit):
    with urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeds the plugin update size limit")
    return data


def update(root, release, download=fetch):
    plugin = root / "Plugin" / DLL
    engine = root / "Game2/SpaceEngineers2.dll"
    if sha256(engine.read_bytes()).hexdigest() != release["engine_sha256"]:
        raise ValueError("The installed SE2 engine does not match this plugin update")
    version = release["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Invalid Y51 release version")
    current = sha256(plugin.read_bytes()).hexdigest()
    if current == release["dll_sha256"]:
        return f"Y51 {version} is already installed"
    package = download(f"{UPDATES}/Y51-SE2Online-{version}-AMP.zip", 32 << 20)
    if sha256(package).hexdigest() != release["zip_sha256"]:
        raise ValueError("Plugin ZIP checksum mismatch")
    with ZipFile(BytesIO(package)) as archive:
        if sorted(archive.namelist()) != sorted([DLL, "LICENSE-Y51.txt"]):
            raise ValueError("This update requires a DLL-only package")
        if archive.getinfo(DLL).file_size > 64 << 20:
            raise ValueError("Plugin exceeds the update size limit")
        binary = archive.read(DLL)
    if sha256(binary).hexdigest() != release["dll_sha256"]:
        raise ValueError("Plugin DLL checksum mismatch")

    backup = plugin.with_suffix(".dll.previous")
    with TemporaryDirectory(dir=plugin.parent) as work:
        new = Path(work) / "new"
        new.write_bytes(binary)
        shutil.copymode(plugin, new)
        previous = Path(work) / "old"
        shutil.copy2(plugin, previous)
        os.replace(previous, backup)
        os.replace(new, plugin)
    return f"Installed Y51 {version}. Previous plugin saved as {backup.name}"


def main(root):
    import fcntl
    if not (root / "Plugin" / DLL).is_file():
        raise ValueError("Prepare the SE2 runtime before updating the plugin")
    state = root / "state"
    state.mkdir(exist_ok=True)
    with (state / ".amp.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The game or an update is running. Stop the game and retry.") from None
        release = json.loads(fetch(f"{UPDATES}/{CHANNEL}", 8192))
        print(update(root, release))


if __name__ == "__main__":
    try:
        main(Path(sys.argv[1]))
    except (OSError, BadZipFile, ValueError, KeyError) as exc:
        sys.exit(f"SE2 AMP update failed: {exc}")
