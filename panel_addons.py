"""Resolve AMP listfile entries and pass them to Y51 and Pulsar at game startup."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile


LIST = "amp-addons.txt"
HUB = "https://codeload.github.com/StarCpt/PluginHub-SE2/zip/refs/heads/main"
STEAM = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
BUILTIN = {"cometworks/linux-compat2", "yellow5111/se2online"}
VERDURE_REPO = "vileider/se2-verdure-planets"
VERDURE_DLL = "VerdurePlanets.dll"
VERDURE_SHA256 = "6d90c74017425348fe3039704862385b77a8119747b365cd55797ac9e4e1f894"


class MissingFile(ValueError):
    pass


def notice(message):
    print("[Dodatki AMP] " + " ".join(str(message).splitlines()), flush=True)


def fetch(url, payload=None):
    req = Request(url, data=payload, headers={"User-Agent": "se2-ubuntu-amp/addons"})
    try:
        with urlopen(req, timeout=10) as response:
            result = response.read(4 * 1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code == 404:
            raise MissingFile(f"Nie znaleziono pliku: {url}") from exc
        raise ValueError(f"Błąd HTTP {exc.code}: {url}") from exc
    except (URLError, OSError) as exc:
        raise ValueError(f"Nie udało się połączyć z {urlsplit(url).hostname}: {exc}") from exc
    if len(result) > 4 * 1024 * 1024:
        raise ValueError("Za duża odpowiedź katalogu dodatków.")
    return result


def source(value):
    value = str(value).strip()
    url = urlsplit(value)
    if url.scheme == "https" and url.netloc.lower() == "github.com":
        parts = url.path.strip("/")
        parts = parts.removesuffix(".git").lower()
        if re.fullmatch(r"[a-z0-9-]+/[a-z0-9_-][a-z0-9_.-]*", parts):
            return "github", parts
    if url.scheme in {"https", "http"} and url.netloc.lower() in {"steamcommunity.com", "www.steamcommunity.com"}:
        if url.path.rstrip("/") in {"/workshop/filedetails", "/sharedfiles/filedetails"}:
            ids = parse_qs(url.query).get("id", [])
            value = ids[0] if len(ids) == 1 else ""
    if re.fullmatch(r"[0-9]{1,20}", value) and 0 < int(value) < 2**64:
        return "steam", str(int(value))
    raise ValueError(f"Podaj ID lub link do pozycji Workshop albo link do repozytorium GitHub: {value[:160]}")


def pulsar_log(root):
    path = root / "pulsar/Modern/info.log"
    try:
        old = path.stat().st_mtime_ns
    except FileNotFoundError:
        old = None

    def forward():
        position = 0
        while True:
            try:
                if path.stat().st_mtime_ns != old:
                    with path.open("rb") as stream:
                        stream.seek(position)
                        for raw in stream:
                            if not raw.endswith(b"\n"):
                                break
                            position += len(raw)
                            line = raw.decode(errors="replace").strip()
                            if any(mark in line for mark in ("Enabled Plugins:", "Initialized ", "[ERROR]")):
                                notice(f"Pulsar: {line}")
            except FileNotFoundError:
                pass
            time.sleep(1)
    return forward


def selected(root):
    path = root / LIST
    try:
        data = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    keys = list(dict.fromkeys(source(line) for line in data.splitlines() if line.strip()))
    if len(keys) > 100:
        raise ValueError("Maksymalnie 100 dodatków na liście.")
    return keys


def steam_items(ids):
    args = {"itemcount": str(len(ids))}
    args.update({f"publishedfileids[{i}]": value for i, value in enumerate(ids)})
    rows = json.loads(fetch(STEAM, urlencode(args).encode()))["response"]["publishedfiledetails"]
    items = {str(row.get("publishedfileid")): row for row in rows}
    for item in ids:
        row = items.get(item, {})
        if row.get("result") != 1 or not row.get("title"):
            raise ValueError(f"Steam {item}: brak publicznej pozycji.")
        title = row["title"]
        if str(row.get("consumer_app_id")) != "1133870":
            raise ValueError(f"Steam {item} ({title}): pozycja do innej gry.")
        tags = {tag.get("tag", "").lower() for tag in row.get("tags", [])}
        if "ugcmodtypetag" not in tags or str(row.get("file_type")) == "2":
            raise ValueError(f"Steam {item} ({title}): Workshop nie oznacza tej pozycji jako pojedynczy mod SE2.")
        yield item, title


def hub_plugins():
    result = {}
    with zipfile.ZipFile(io.BytesIO(fetch(HUB))) as archive:
        for entry in archive.infolist():
            if not re.fullmatch(r"[^/]+/Plugins/[^/]+\.xml", entry.filename):
                continue
            if entry.file_size > 128 * 1024:
                raise ValueError("Za duży manifest PluginHub.")
            node = ET.fromstring(archive.read(entry))
            if node.get("{http://www.w3.org/2001/XMLSchema-instance}type") == "GitHubPlugin":
                repo = node.findtext("RepoId", "").strip().lower()
                if repo:
                    result.setdefault(repo, []).append(node)
    return result


def legacy_verdure():
    # v5 shipped this pin and stored its DLL directly under amp-plugins.
    return {"schema": 1, "name": "Verdure Planets 9P", "version": "0.1.0",
            "dll": f"dist/0.1.0/{VERDURE_DLL}", "sha256": VERDURE_SHA256}


def addon_manifest(repo):
    try:
        data = fetch(f"https://raw.githubusercontent.com/{repo}/HEAD/se2-addon.json")
    except MissingFile:
        return legacy_verdure() if repo == VERDURE_REPO else None
    entry = json.loads(data)
    if not isinstance(entry, dict) or type(entry.get("schema")) is not int or entry["schema"] != 1:
        raise ValueError(f"{repo}: nieobsługiwana wersja se2-addon.json.")
    for key in ("name", "version"):
        value = entry.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 120 or not value.isprintable():
            raise ValueError(f"{repo}: nieprawidłowe pole {key} w se2-addon.json.")
    dll = entry.get("dll")
    if not isinstance(dll, str) or not re.fullmatch(r"(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-][A-Za-z0-9_.-]*[.]dll", dll, re.I):
        raise ValueError(f"{repo}: podaj względną ścieżkę DLL w repozytorium.")
    if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]):
        raise ValueError(f"{repo}: nieprawidłowa suma SHA-256.")
    return {key: entry[key] for key in ("schema", "name", "version", "dll", "sha256")}


def resolve(keys):
    ids = [value for kind, value in keys if kind == "steam"]
    steam = dict(steam_items(ids)) if ids else {}
    hub = None
    resolved = []
    for kind, key in keys:
        if kind == "steam":
            name, ident = steam[key], key
            notice(f"Znaleziono w Steam Workshop: {name} [ID {key}].")
        else:
            if key in BUILTIN:
                raise ValueError(f"{key} jest już częścią tego serwera.")
            manifest = addon_manifest(key)
            if manifest is not None:
                resolved.append({"source": kind, "key": key, "id": Path(manifest["dll"]).name,
                                 "name": manifest["name"], "manifest": manifest})
                continue
            if hub is None:
                hub = hub_plugins()
            matches = hub.get(key, [])
            if len(matches) != 1:
                raise ValueError(f"{key}: brak jednoznacznego pluginu w katalogu Pulsar PluginHub-SE2.")
            node = matches[0]
            name, ident = node.findtext("FriendlyName"), node.findtext("Id")
            if not name or not ident:
                raise ValueError(f"{key}: niepełny manifest pluginu.")
            for tag, required in (("Platforms", "linux"), ("Runtimes", "coreclr")):
                supported = node.findtext(tag, "").strip()
                if supported and required not in [v.strip().lower() for v in re.split("[;,]", supported)]:
                    raise ValueError(f"{name}: manifest {tag}={supported} wyklucza ten serwer.")
            notice(f"Znaleziono w Pulsar PluginHub: {name} [{key}].")
        resolved.append({"source": kind, "key": key, "id": ident, "name": name})
    return resolved


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(data)
            stream.close()
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def check(root, force=False):
    if os.name == "posix":
        import fcntl
        state = root / "state"
        state.mkdir(parents=True, exist_ok=True)
        with (state / "amp-addons.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return check_selected(root, force)
    return check_selected(root, force)


def check_selected(root, force):
    keys = selected(root)
    cache = root / "state/amp-addons-cache.json"
    previous = json.loads(cache.read_bytes()) if cache.exists() else []
    known = {} if force else {(r["source"], r["key"]): r.copy() for r in previous}
    legacy = known.get(("github", VERDURE_REPO))
    if legacy and "manifest" not in legacy and legacy["id"] == VERDURE_DLL:
        legacy["manifest"] = legacy_verdure()
    for row in resolve([key for key in keys if key not in known]):
        known[(row["source"], row["key"])] = row
    rows = [known[key] for key in keys]
    # Stage all DLLs before switching versions in the cache.
    native_plugins(root, rows)
    if keys != selected(root):
        raise ValueError("Lista zmieniła się w trakcie sprawdzania. Sprawdź nową listę.")
    if rows != previous:
        atomic(cache, json.dumps(rows).encode())
    return rows


def live_preview(root, stamp):
    path = root / LIST
    while True:
        time.sleep(1)
        try:
            current = path.read_bytes() if path.is_file() else b""
            if current == stamp:
                continue
            stamp = current
            rows = check(root)
            notice(f"Sprawdzono listę ({len(rows)}). Zmiany wejdą w życie przy następnym starcie gry.")
        except (ValueError, KeyError, OSError, ET.ParseError, zipfile.BadZipFile) as exc:
            notice(f"Błąd sprawdzania listy: {exc}")


def apply_mods(cfg, rows, previous):
    # Track only entries added by AMP. Keep legacy mods and their original representation.
    wanted = [r["id"] for r in rows if r["source"] == "steam"]
    if not wanted and not previous:
        return []
    def workshop_key(entry):
        try:
            return source(entry)[1]
        except ValueError:
            return str(entry)
    kept = [e for e in (cfg.get("Mods") or []) if workshop_key(e) not in previous]
    existing = {workshop_key(e) for e in kept}
    owned = [key for key in wanted if key not in existing]
    cfg["Mods"] = kept + owned
    return owned


def plugin_path(root, row):
    manifest = row["manifest"]
    return root / "state/amp-plugins" / row["key"] / manifest["sha256"] / Path(manifest["dll"]).name


def native_plugins(root, rows):
    paths = []
    for row in rows:
        repo = row["key"]
        if "manifest" not in row:
            continue
        manifest = row["manifest"]
        digest = manifest["sha256"]
        path = plugin_path(root, row)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            old = root / "state/amp-plugins" / VERDURE_DLL
            data = old.read_bytes() if repo == VERDURE_REPO and digest == VERDURE_SHA256 and old.is_file() else b""
            if hashlib.sha256(data).hexdigest() != digest or not data:
                data = fetch(f"https://raw.githubusercontent.com/{repo}/HEAD/{manifest['dll']}")
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"{repo}: niezgodna suma SHA-256 biblioteki.")
            atomic(path, data)
        notice(f"{row['name']} {manifest['version']}: biblioteka DLL sprawdzona.")
        paths.append(path)
    return paths


def pulsar_profile(root, rows):
    plugins = [row for row in rows if row["source"] == "github" and "manifest" not in row]
    if not plugins:
        return None
    current = root / "pulsar/Modern/Profiles/Current.xml"
    if current.exists():
        profile = ET.parse(current).getroot()
        if profile.tag != "Profile" or any(profile.find(t) is None for t in ("Name", "GitHub", "DevFolder", "Local", "Mods")):
            raise ValueError("Nieprawidłowy profil Pulsara Current.xml.")
    else:
        profile = ET.Element("Profile")
        for tag in ("Name", "GitHub", "DevFolder", "Local", "Mods"):
            ET.SubElement(profile, tag)
    profile.find("Name").text = "AMP-addons"
    github = profile.find("GitHub")
    known = {node.text for node in github.findall("GitHubPluginConfig/Id")}
    for plugin in plugins:
        if plugin["id"] not in known:
            entry = ET.SubElement(github, "GitHubPluginConfig")
            ET.SubElement(entry, "Id").text = plugin["id"]
            known.add(plugin["id"])
    target = root / "state/amp-pulsar-profile.xml"
    atomic(target, ET.tostring(profile, encoding="utf-8", xml_declaration=True))
    return target
