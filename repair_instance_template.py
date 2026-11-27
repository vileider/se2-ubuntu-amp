"""Apply the SE2 v5 panel template to one existing AMP Docker instance."""
import argparse
import codecs
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent
GAME = Path("SpaceEngineers2")
LAUNCHER = "se2-panel-v3.pyz"
DIGEST = "808776e7981f4459e5e1042d17d16a8768db3999aae6a92fe17919a79ed6726a"


def kvp(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def update_kvp(data, template, stages):
    text = data.decode("utf-8-sig")
    current = kvp(text)
    if (current.get("Meta.ConfigRoot") != "y51-se2.kvp"
            or current.get("App.ExecutableLinux") != template["App.ExecutableLinux"]
            or Path(current["App.BaseDirectory"]) != GAME):
        raise ValueError("Expected the Linux Y51 SE2 template and SpaceEngineers2 base directory")
    if current.get("Meta.ConfigVersion") not in {"1", "2", "3", "4", "5"}:
        raise ValueError("Unrecognised template version")
    command = current["App.CommandLineArgs"]
    if not re.fullmatch(r"(?:-u /opt/se2-amp/service\.py|-I -u \./se2-panel-v[123]\.pyz)"
                        r" \{\{\$ApplicationPort1\}\} \{\{\$ApplicationPort2\}\}", command):
        raise ValueError("Instance uses a custom launch command; review it before migration")

    changes = {"Meta.ConfigVersion": template["Meta.ConfigVersion"],
               "App.CommandLineArgs": template["App.CommandLineArgs"]}
    env = json.loads(current.get("App.EnvironmentVariables", "{}"))
    merged = {**json.loads(template["App.EnvironmentVariables"]), **env}
    if merged != env:
        changes["App.EnvironmentVariables"] = compact(merged)

    previous = json.loads(current.get("App.PreStartStages", "[]"))
    names = {s["UpdateStageName"] for s in stages}
    first = next((i for i, step in enumerate(previous) if step.get("UpdateStageName") in names),
                 len(previous))
    following = [step for step in previous[first:] if step.get("UpdateStageName") not in names]
    merged_stages = previous[:first] + stages + following
    if previous != merged_stages:
        changes["App.PreStartStages"] = compact(merged_stages)
    if json.loads(current.get("App.UpdateSources", "[]")) == []:
        changes["App.UpdateSources"] = compact(json.loads((SOURCE / "y51-se2updates.json").read_bytes()))

    newline = "\r\n" if "\r\n" in text else "\n"
    for key, value in changes.items():
        if current.get(key) == value:
            continue
        pattern = rf"^{re.escape(key)}=[^\r\n]*"
        if key in current:
            text, count = re.subn(pattern, lambda _: f"{key}={value}", text, flags=re.MULTILINE)
            if count != 1:
                raise ValueError(f"Duplicate {key} in GenericModule.kvp")
        else:
            if text and not text.endswith("\n"):
                text += newline
            text += f"{key}={value}{newline}"
    return (codecs.BOM_UTF8 if data.startswith(codecs.BOM_UTF8) else b"") + text.encode()


def add_definitions(data, source_name, key, required):
    """Retain existing UI definitions and verify the controls used by this launcher."""
    rows = [] if data is None else json.loads(data)
    if not isinstance(rows, list):
        raise ValueError(f"Expected an array for {source_name}")
    existing = {row.get(key): row for row in rows}
    additions = []
    for entry in json.loads((SOURCE / source_name).read_bytes()):
        name = entry[key]
        if name not in existing:
            additions.append(entry)
        elif name in required:
            # AMP can add defaults when serialising; check the fields that wire each control.
            for prop in required[name]:
                if existing[name].get(prop) != entry[prop]:
                    raise ValueError(f"Conflicting {name}.{prop} in {source_name}")
    if data is not None and not additions:
        return data
    return (json.dumps(rows + additions, ensure_ascii=False, indent=2) + "\n").encode()


def plan(instance):
    template = kvp((SOURCE / "y51-se2.kvp").read_text())
    stages = json.loads((SOURCE / "y51-se2start.json").read_bytes())
    launcher = (SOURCE / "updates" / LAUNCHER).read_bytes()
    if (template["Meta.ConfigVersion"] != "5" or hashlib.sha256(launcher).hexdigest() != DIGEST
            or DIGEST not in stages[-1]["UpdateSourceArgs"]):
        raise ValueError("Checkout does not contain the matching v5 template and verified launcher")

    def read(rel):
        path = instance / rel
        if path != path.resolve():
            raise ValueError(f"Unsupported symbolic link: {path}")
        return path.read_bytes() if path.exists() else None

    changes = {}
    def record(rel, old, new):
        if old != new:
            changes[Path(rel)] = new

    cfg = "GenericModule.kvp"
    data = read(cfg)
    if data is None:
        raise ValueError("Missing GenericModule.kvp")
    record(cfg, data, update_kvp(data, template, stages))
    for target, source, key, checks in (
        ("configmanifest.json", "y51-se2config.json", "FieldName", {
            "WorldSaveRequest": ("ParamFieldName", "InputType"),
            "PanelAddons": ("Special", "InputType"),
            "StreamingRadiusMeters": ("InputType",)}),
        ("metaconfig.json", "y51-se2metaconfig.json", "ConfigFile", {
            "amp-world-save.kvp": ("Subsections", "ConfigType", "ConfigFormat")}),
    ):
        data = read(target)
        record(target, data, add_definitions(data, source, key, checks))
    record(GAME / LAUNCHER, read(GAME / LAUNCHER), launcher)
    return changes


def install(instance, changes):
    if not changes:
        return
    backup = Path(tempfile.mkdtemp(prefix=".before-se2-panel-v5-", dir=instance))
    print(f"Backup: {backup}", flush=True)
    with tempfile.TemporaryDirectory(dir=instance) as temp:
        staged = Path(temp)
        for rel, data in changes.items():
            path = instance / rel
            new = staged / rel
            new.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup / rel)
                shutil.copy2(path, new)
            new.write_bytes(data)
        installed = []
        try:
            for rel in changes:
                (staged / rel).replace(instance / rel)
                installed.append(rel)
            if plan(instance):
                raise ValueError("Effective template still differs after installation")
        except BaseException:
            for rel in reversed(installed):
                original = backup / rel
                if original.exists():
                    shutil.copy2(original, staged / rel)
                    (staged / rel).replace(instance / rel)
                else:
                    (instance / rel).unlink()
            raise


def running(name):
    names = subprocess.check_output(["docker", "ps", "--filter", f"name=^/AMP_{name}$",
                                     "--format", "{{.Names}}"], text=True)
    return f"AMP_{name}" in names.splitlines()


def repair(name, instances):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name.startswith("-"):
        raise ValueError("Invalid AMP instance name")
    root = (instances / name).resolve()
    if ((root / "GenericModule.kvp").stat().st_uid != os.geteuid()
            or not (root / GAME / "Game2/SpaceEngineers2.dll").is_file()):
        raise ValueError("Run as the owner of the prepared SE2 instance")
    was_running = running(name)
    restart = False
    try:
        with (root / GAME / "state/.amp.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Stop the game in AMP and wait for its final save") from None
            if plan(root):
                if was_running:
                    subprocess.run(["ampinstmgr", "-q", name], check=True)
                if running(name):
                    raise ValueError("Instance panel is still running")
                restart = was_running
                install(root, plan(root))  # AMP may write its KVP during shutdown.
    finally:
        if restart:
            subprocess.run(["ampinstmgr", "-s", name], check=True)
    if plan(root):
        raise ValueError("Panel files changed again after restarting the instance")
    if was_running:
        mount = subprocess.check_output(["docker", "inspect", "--format",
            '{{range .Mounts}}{{if eq .Destination "/AMP"}}{{.Source}}{{end}}{{end}}',
            f"AMP_{name}"], text=True).strip()
        if not running(name) or not mount or Path(mount).resolve() != root.resolve():
            raise ValueError("Could not confirm the repaired directory is mounted at /AMP")
    print("SE2_TEMPLATE_V5_OK (" + ("panel running" if was_running else "offline instance") + ")")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance")
    try:
        repair(parser.parse_args().instance, Path.home() / ".ampdata/instances")
    except (OSError, KeyError, TypeError, ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(f"SE2 AMP: {exc}")
