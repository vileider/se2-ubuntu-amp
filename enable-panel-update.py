"""Enable the Y51 Update button on an existing Docker instance owned by this AMP user."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


SETTING = re.compile(rb"^App\.UpdateSources=([^\r\n]*)", re.MULTILINE)


def setting(data):
    matches = list(SETTING.finditer(data))
    if len(matches) != 1:
        raise ValueError("Expected exactly one App.UpdateSources setting")
    return matches[0]


def needs_change(data, stages):
    current = json.loads(setting(data)[1])
    if current == stages:
        return False
    if current != []:
        raise ValueError("Instance already has custom update stages; preserving them")
    return True


def install(cfg, stages):
    data = cfg.read_bytes()
    if not needs_change(data, stages):
        return
    start, end = setting(data).span(1)
    encoded = json.dumps(stages, separators=(",", ":")).encode()
    with TemporaryDirectory(dir=cfg.parent) as tmp:
        new = Path(tmp) / cfg.name
        shutil.copy2(cfg, new)
        new.write_bytes(data[:start] + encoded + data[end:])
        backup = cfg.with_name(f"{cfg.name}.before-y51-{time.time_ns()}")
        shutil.copy2(cfg, backup)
        new.replace(cfg)
    print(f"Saved previous configuration: {backup}", flush=True)


def running(container):
    names = subprocess.check_output(
        ["docker", "ps", "--filter", f"name=^/{re.escape(container)}$", "--format", "{{.Names}}"],
        text=True)
    return container in names.splitlines()


def main(instance):
    import fcntl
    if not re.fullmatch(r"[A-Za-z0-9_-]+", instance):
        raise ValueError("Invalid instance name")
    directory = Path.home() / ".ampdata/instances" / instance
    cfg = directory / "GenericModule.kvp"
    game = directory / "SpaceEngineers2"
    if cfg.stat().st_uid != os.geteuid() or not (game / "Game2/SpaceEngineers2.dll").is_file():
        raise ValueError("Run as the AMP account that owns this prepared SE2 instance")
    stages = json.loads(Path(__file__).with_name("y51-se2updates.json").read_bytes())
    if [s["UpdateSource"] for s in stages] != ["FetchURL", "Executable"]:
        raise ValueError("Expected the Y51 FetchURL and Executable stages")

    container = f"AMP_{instance}"
    was_running = running(container)
    if needs_change(cfg.read_bytes(), stages):
        with ExitStack() as cleanup:
            with (game / "state/.amp.lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("Stop the game in AMP and wait for its final save, then retry") from None
                if was_running:
                    subprocess.run(["ampinstmgr", "-q", instance], check=True)
                if running(container):
                    raise ValueError("The instance container is still running")
                if was_running:
                    # Also restore the instance if writing the configuration fails.
                    cleanup.callback(subprocess.run, ["ampinstmgr", "-s", instance], check=True)
                install(cfg, stages)

    if was_running:
        entry = subprocess.check_output(
            ["docker", "exec", container, "grep", "^App[.]UpdateSources=", "/AMP/GenericModule.kvp"])
    else:
        entry = cfg.read_bytes()
    if needs_change(entry, stages):
        raise ValueError("App.UpdateSources is still empty after applying the change")
    print("SE2_UPDATE_STAGES_OK (" + ("container" if was_running else "offline instance") + ")")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance")
    try:
        main(parser.parse_args().instance)
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"SE2 AMP: {exc}")
