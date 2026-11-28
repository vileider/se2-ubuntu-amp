"""Launch Y51 inside an AMP instance and connect its file console to AMP's stdio."""
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time

import panel_addons


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def check_files(root):
    required = ("Game2/SpaceEngineers2.dll", "Game2/steam_appid.txt",
                "GameData/Vanilla/Vanilla.vrgproj", "VRage/GameData/Engine/Engine.vrgproj",
                "pulsar/Modern.bin", "pulsar/Modern/Local/linux-compat/plugin.dll",
                "pulsar/Modern/Local/linux-compat/plugin.xml", "Plugin/Y51_SE2Online.dll",
                "native/lib/libVRage.Physics.Native.so")
    for name in required:
        if not (root / name).is_file():
            raise ValueError(f"Brak {root / name}. Wgraj pliki przygotowane przez prepare-runtime.py.")
    if (root / "Game2/steam_appid.txt").read_text().strip() != "1133870":
        raise ValueError("Oczekiwano Steam AppID 1133870.")
    if not os.access(root / "pulsar/Modern.bin", os.X_OK):
        raise ValueError("Nadaj prawo wykonania plikowi pulsar/Modern.bin.")


def configure(root, game, query, settings, addons=()):
    if not 1 <= game < 65535 or query != game + 1:
        raise ValueError("W ustawieniach portów AMP ustaw Steam query na port gry + 1.")
    session = settings["SE2_SESSION_NAME"].strip()
    world = settings["SE2_WORLD_NAME"].strip()
    scenario = settings["SE2_START_CONTENT"]
    players = int(settings["SE2_MAX_PLAYERS"])
    delay = int(settings["SE2_SAVE_INTERVAL"])
    password = settings["SE2_PASSWORD"]
    advertise = settings["SE2_ADVERTISE"].lower()
    if not 1 <= len(session) <= 100 or not world:
        raise ValueError("Podaj nazwę świata i nazwę serwera (do 100 znaków).")
    if (len(password) > 64 or any(not c.isprintable() for c in password)
            or not 2 <= players <= 384 or (delay != 0 and delay < 30)
            or advertise not in ("true", "false")):
        raise ValueError("Sprawdź hasło, limit graczy, widoczność lub odstęp między zapisami w AMP.")
    existing = scenario == "ExistingSave"
    if existing:
        saves = root / "profile/AppData/SaveGames"
        selected = saves / world
        if (world.startswith(".") or "/" in world or "\\" in world or selected.is_symlink()
                or selected.resolve().parent != saves.resolve()
                or not (selected / ".container-info").is_file()
                or not any((selected / name).is_file() and (selected / name).stat().st_size
                           for name in ("savegame.vrb", "savegame"))):
            raise ValueError(f"Brak zapisu w profilu AMP: {world}. Sprawdz nazwe folderu w SaveGames.")
    elif scenario not in ("EmptyWorld", "ClassicSurvival", "Survival"):
        raise ValueError("Nieznany świat startowy.")
    elif not (root / "GameData/Vanilla/Worlds" / scenario / "savegame.vrb").is_file():
        raise ValueError(f"Brak danych scenariusza {scenario}.")

    state_dir = root / "state"
    state_dir.mkdir(exist_ok=True)
    path = state_dir / "server.json"
    cfg = read_json(path) if path.exists() else {}
    previous = state_dir / "dedicated-state.json"
    if existing:
        cfg.update(SaveId=world, StartWorkshop="")
        scenario = ""
    elif previous.exists():
        saved = read_json(previous)
        if (saved.get("WorldName"), saved.get("SourceContent")) != (world, scenario):
            raise ValueError("Ten profil ma zapis. Przywróć nazwę świata i scenariusz w AMP albo użyj nowej instancji.")
    cfg.update(WorldName=world, StartContent=scenario, SaveOnExit=True, RconEnabled=False,
               SaveIntervalSeconds=delay, EnableAutosave=delay > 0)
    cfg.setdefault("AutoUpdate", {})["Enabled"] = False
    network = cfg.setdefault("Network", {})
    radius = settings.get("SE2_STREAMING_RADIUS_METERS", "").strip()
    if radius:
        try:
            metres = int(radius)
        except ValueError:
            raise ValueError("Podaj promień synchronizacji w pełnych metrach.") from None
        if metres != 0 and not 1000 <= metres <= 1_000_000:
            raise ValueError("Promień synchronizacji musi wynosić 0 lub od 1000 do 1000000 metrów.")
        network["StreamingRadiusMeters"] = metres
    network.update(SessionName=session, Password=password or network.get("Password", ""), MaxPlayers=players,
                                         Advertise=advertise == "true", AllowDirectIp=True,
                                         ListenPort=game, BindAddress="0.0.0.0")
    ledger = state_dir / "amp-workshop-owned.json"
    previous_ids = read_json(ledger) if ledger.exists() else []
    owned = panel_addons.apply_mods(cfg, addons, previous_ids)
    if owned != previous_ids:
        # A retry must recognise both generations if the process stops between replacements.
        panel_addons.atomic(ledger, json.dumps(sorted(set(previous_ids + owned))).encode())
    panel_addons.atomic(path, json.dumps(cfg, ensure_ascii=False, indent=2).encode())
    if owned != previous_ids:
        panel_addons.atomic(ledger, json.dumps(owned).encode())
    return path


def launch(root, config, plugin_profile=None, native_plugins=()):
    profile, home = root / "profile", root / "home"
    profile.mkdir(exist_ok=True)
    steam = home / ".steam"
    steam.mkdir(parents=True, exist_ok=True)
    if not (steam / "sdk64").is_symlink() and not (steam / "sdk64").exists():
        (steam / "sdk64").symlink_to("/opt/se2-steam/sdk64", target_is_directory=True)
    native = str(root / "native/lib")
    env = {k: v for k, v in os.environ.items() if k != "SE2_PASSWORD"}
    env.update(HOME=str(home), DOTNET_ROOT="/opt/se2-amp/dotnet", COMPlus_ReadyToRun="0",
               SDL_VIDEODRIVER="offscreen", SteamAppId="1133870", SteamGameId="1133870",
               SE2_NATIVE_DIR=native, LD_LIBRARY_PATH=native + ":/opt/se2-steam/sdk64")
    libraries = [root / "Plugin/Y51_SE2Online.dll", *native_plugins]
    args = [root / "pulsar/Modern.bin", "-game2", root / "Game2", "-sources", "-noUpdate",
            "-noPrompt", "-noSplash", "-lazySteam", "-noStats", "-noDiscord", "-allowMultiple",
            "-noPillars", "-localDedicatedServer", f"-appData:{profile}",
            "-plugins:" + ";".join(map(str, libraries)), f"-y51dedicated:{config}",
            "-y51headless", "-hiddenWindow"]
    if plugin_profile is not None:
        args.extend(["-profile", str(plugin_profile)])
    return subprocess.Popen(args, cwd=root / "Game2", env=env, stdin=subprocess.DEVNULL)


def publish_command(path, line):
    # Y51 reads and then deletes one command per poll. Never replace a pending command.
    if path.exists():
        return False
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        f.write(line + "\n")
    try:
        if os.name == "nt":
            os.rename(f.name, path)  # Windows rename fails if the target exists.
        else:
            os.link(f.name, path)
        return True
    except FileExistsError:
        return False
    finally:
        Path(f.name).unlink(missing_ok=True)


def safe_line(value):
    return " ".join(str(value).splitlines()).strip()


def save_switch(path):
    """Ignore missing or partial settings while AMP rewrites the KVP."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (FileNotFoundError, UnicodeError):
        return None
    for line in content.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "WorldSaveRequest":
            return {"true": True, "false": False}.get(value.strip().lower())
    return None


def supervise(child, state_dir, commands, console_offset):
    status_file = state_dir / "dedicated-status.json"
    console_file = state_dir / "dedicated-console.jsonl"
    cmd_file = state_dir / "dedicated-command.txt"
    switch_file = state_dir.parent / "amp-world-save.kvp"
    switch_value = save_switch(switch_file) or False
    panel_save = False
    stamp = None
    state = {}
    active_players = {}
    message = None
    ready = False
    requested_stop = False
    pending = None
    while True:
        code = child.poll()
        try:
            st = status_file.stat().st_mtime_ns
            if st != stamp:
                report = read_json(status_file)
                if report.get("ProcessId") == child.pid:
                    state = report
                stamp = st
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        if console_file.is_file():
            with console_file.open("rb") as f:
                f.seek(console_offset)
                for raw in f:
                    if not raw.endswith(b"\n"):
                        break
                    console_offset += len(raw)
                    try:
                        row = json.loads(raw)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if row.get("ProcessId") == child.pid:
                        line = safe_line(row.get("Message", ""))
                        print("[Y51] " + line, flush=True)
                        if line.startswith("dedicated: durable native save completed "):
                            world = line.removeprefix("dedicated: durable native save completed ")
                            print(f"[AMP] Zapisano świat: {world}", flush=True)
        error = state.get("Error") or state.get("NetworkFailure")
        if error and error != message:
            print("[Y51] " + safe_line(error), flush=True)
        message = error
        if (not ready and not error and state.get("WorldLoaded") and
                str(state.get("Status", "")).startswith("Hosting ") and int(state.get("NetworkSteamIdentity") or 0)):
            print("SE2_HOSTING_READY", flush=True)
            ready = True
        players = {str(p["SteamId"]): safe_line(p["Name"]) for p in state.get("Players", [])
                   if p.get("State") == "Playing" and p.get("SteamId") and p.get("Name")}
        for uid in players.keys() - active_players.keys():
            print(f"SE2_PLAYER_JOIN {uid} {players[uid]}", flush=True)
        for uid in active_players.keys() - players.keys():
            print(f"SE2_PLAYER_LEAVE {uid} {active_players[uid]}", flush=True)
        active_players = players
        if code is not None:
            saved = (state.get("Status") == "Shutdown complete" and state.get("FinalSaveCompleted")
                     and state.get("LastSaveUtc") and not state.get("Error"))
            print(f"[Y51] Kod procesu {code}. Potwierdzony zapis końcowy: {bool(saved)}.", flush=True)
            if requested_stop and saved and code in (0, -9, 137):
                return 0
            return (128 - code if code < 0 else code) or int(requested_stop and not saved)
        value = save_switch(switch_file)
        if value is not None and value != switch_value:
            switch_value = value
            if state.get("WorldLoaded") and not requested_stop:
                panel_save = True
            else:
                print("[AMP] Zapis pominięty. Świat musi być załadowany i działać.", flush=True)
        if pending is None:
            try:
                pending = commands.get_nowait()
            except queue.Empty:
                pass
        if pending is not None:
            if publish_command(cmd_file, pending):
                requested_stop |= pending.lower() in ("stop", "stop now")
                pending = None
        elif panel_save and not requested_stop and publish_command(cmd_file, "save"):
            panel_save = False
            print("[AMP] Wysłano polecenie zapisu świata.", flush=True)
        time.sleep(0.2)


def main():
    if len(sys.argv) == 3 and sys.argv[1] in ("--check-addons", "--update-addons"):
        rows = panel_addons.check(Path(sys.argv[2]).resolve(), force=True)
        panel_addons.notice(f"Sprawdzono listę ({len(rows)}). Gotowa do startu gry.")
        return 0
    root = Path.cwd()
    check_files(root)
    if sys.argv[1:] == ["--check"]:
        print("SE2_AMP_FILES_OK")
        return 0
    import fcntl
    state_dir = root / "state"
    state_dir.mkdir(exist_ok=True)
    with (state_dir / ".amp.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        listfile = root / panel_addons.LIST
        stamp = listfile.read_bytes() if listfile.is_file() else b""
        addons = panel_addons.check(root)
        plugin_profile = panel_addons.pulsar_profile(root, addons)
        native_plugins = [panel_addons.plugin_path(root, row) for row in addons if "manifest" in row]
        config = configure(root, int(sys.argv[1]), int(sys.argv[2]), os.environ, addons)
        for name in ("dedicated-command.txt", "dedicated-status.json"):
            p = state_dir / name
            if p.exists():
                p.replace(state_dir / (name + ".previous"))
        log = state_dir / "dedicated-console.jsonl"
        offset = log.stat().st_size if log.exists() else 0
        commands = queue.Queue()
        def read_stdin():
            for line in sys.stdin:
                if line.strip():
                    commands.put(line.strip())
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: commands.put("stop now"))
        read_pulsar = panel_addons.pulsar_log(root) if plugin_profile else None
        child = launch(root, config, plugin_profile, native_plugins)
        if read_pulsar:
            threading.Thread(target=read_pulsar, daemon=True).start()
        threading.Thread(target=panel_addons.live_preview, args=(root, stamp), daemon=True).start()
        threading.Thread(target=read_stdin, daemon=True).start()
        return supervise(child, state_dir, commands, offset)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, IndexError, panel_addons.ET.ParseError, panel_addons.zipfile.BadZipFile) as exc:
        sys.exit(f"SE2 AMP: {exc}")
