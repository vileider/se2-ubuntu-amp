"""Resolve AMP listfile entries and pass them to Y51 and Pulsar at game startup."""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import URLError
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


def notice(message):
    print("[Dodatki AMP] " + " ".join(str(message).splitlines()), flush=True)


def fetch(url, payload=None):
    req = Request(url, data=payload, headers={"User-Agent": "se2-ubuntu-amp/addons"})
    try:
        with urlopen(req, timeout=10) as response:
            result = response.read(4 * 1024 * 1024 + 1)
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
        if re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", parts):
            return "github", parts.removesuffix(".git").lower()
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


def resolve(keys):
    ids = [value for kind, value in keys if kind == "steam"]
    steam = dict(steam_items(ids)) if ids else {}
    hub = hub_plugins() if any(kind == "github" and key != VERDURE_REPO for kind, key in keys) else {}
    resolved = []
    for kind, key in keys:
        if kind == "steam":
            name, ident = steam[key], key
            notice(f"Znaleziono w Steam Workshop: {name} [ID {key}].")
        elif kind == "github" and key == VERDURE_REPO:
            name, ident = "Verdure Planets 9P", VERDURE_DLL
            notice(f"Rozpoznano plugin serwera: {name} [{key}].")
        else:
            if key in BUILTIN:
                raise ValueError(f"{key} jest już częścią tego serwera.")
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
    keys = selected(root)
    cache = root / "state/amp-addons-cache.json"
    if not force and cache.exists():
        rows = json.loads(cache.read_bytes())
        if [(r["source"], r["key"]) for r in rows] == keys:
            return rows
    rows = resolve(keys)
    if keys != selected(root):
        raise ValueError("Lista zmieniła się w trakcie sprawdzania. Sprawdź nową listę.")
    if keys or cache.exists():
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
            rows = check(root, force=True)
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


def native_plugins(root, rows):
    if not any(row["source"] == "github" and row["key"] == VERDURE_REPO for row in rows):
        return []
    path = root / "state/amp-plugins" / VERDURE_DLL
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != VERDURE_SHA256:
        url = f"https://raw.githubusercontent.com/{VERDURE_REPO}/main/dist/0.1.0/{VERDURE_DLL}"
        data = fetch(url)
        if hashlib.sha256(data).hexdigest() != VERDURE_SHA256:
            raise ValueError("Verdure Planets: pobrany DLL ma niezgodna sume SHA-256.")
        atomic(path, data)
    notice("Verdure Planets 9P: DLL sprawdzony i gotowy.")
    return [path]


def pulsar_profile(root, rows):
    plugins = [row for row in rows if row["source"] == "github" and row["key"] != VERDURE_REPO]
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
