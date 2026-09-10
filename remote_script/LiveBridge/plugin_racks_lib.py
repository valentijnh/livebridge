"""Generated rack presets that expose any plug-in parameter to Live (stdlib only).

Live 12.4.5 exposes a plug-in's parameters only through the device's Configure
list, and big plug-ins (Serum 2: 0 of 2623) start with an empty one — no API
fills it.  But Live's rack presets (``.adg``) store that list as
``<PluginParameterSettings>`` entries holding nothing but the plug-in's own
``ParameterId`` (VST3 ``ParamID`` / AU ``AudioUnitParameterID``); Live resolves
the names itself when the preset loads (verified on Live 12.4.5 with Serum 2).
So LiveBridge *writes* a rack preset around the plug-in that lists the wanted
ids (max 128), lets Live index it and loads it: every listed parameter is then
an ordinary exposed ``DeviceParameter`` (``devices.set_parameter``,
automation, rack macros).

This module is the file side of that trick and never touches the LOM:

* paths — the User Library (two folders above the Remote Script folder, else
  Live's ``Library.cfg``), ``<User Library>/LiveBridge/Racks`` (generated
  presets; Live's browser only lists top-level User Library folders, verified)
  and ``.../LiveBridge/Maps`` (probed name -> ParameterId maps);
* plug-in identities — VST3 class ids from ``<bundle>.vst3/Contents/Resources/
  moduleinfo.json`` (falls back to Live's plug-in database), Audio Unit
  component codes from ``<bundle>.component/Contents/Info.plist``;
* plug-in state — ``.vstpreset`` chunks (``Comp`` = Live's ``<ProcessorState>``,
  ``Cont`` = ``<ControllerState>``, byte-identical, verified), Live presets that
  hold the plug-in, ``.aupreset`` files as the AU ``<Buffer>``; Xfer's container
  format (Serum 2 states and ``.SerumPreset`` files) is parsed for checks only —
  Serum refuses preset payloads as a state;
* the rack XML itself (Instrument Rack for instruments, Audio Effect Rack for
  effects — a bare ``.adv`` with only the plug-in does not load);
* :class:`Prober` — the pure logic that finds a plug-in's ParameterIds by
  loading probe racks of 128 candidate ids and reading back the names.
"""

import gzip
import hashlib
import json
import os
import plistlib
import re
import struct
import sys
import time

#: Live's limit of configured plug-in parameters per device.
MAX_PARAMETERS = 128
#: Rack macros (Live 12 racks have 16).
MAX_MACROS = 16
#: Macro mapping range of a plug-in parameter.  Live stores it in the plug-in's
#: own value units (Hz, ms, %) and clamps it to the parameter's range, so a huge
#: range maps the macro linearly over the whole normalized 0..1 range (verified:
#: macro/127 == value for Serum 2's Freq, Attack, Pan, Type, Unison).  An empty
#: range with a macro index crashed Live 12.4.5.
MACRO_RANGE = (-1000000000, 1000000000)
#: Serum 2 VST3 ParameterIds are ``block * BLOCK + index``.
BLOCK = 1000000
#: Name Live gives a ParameterId the plug-in does not know ("Parameter #7").
INVALID_NAME_RE = re.compile(r"^Parameter #\d+$")
#: VST3 MIDI-mapping proxies (Serum 2 has 16 x 130 of them) — never worth exposing.
MIDI_PROXY_RE = re.compile(r"^(CC\d+ Chan \d+|Pitch Bend Chan \d+|Aftertouch Chan \d+)$")

#: Why ``.SerumPreset`` files cannot be embedded (tested on Live 12.4.5 with the preset's
#: payload verbatim and re-wrapped as a processor state: Live logged "couldn't set
#: processor state").
SERUM_PRESET_REFUSED = ("Serum 2 rejects .SerumPreset data as a VST3 plug-in state — load "
                        "the preset in Serum's own browser, or save the sound as a Live "
                        "preset (.adv) / .vstpreset and pass that file")

_LIVE_HEADER = ('<?xml version="1.0" encoding="UTF-8"?>\n<Ableton MajorVersion="5" '
                'MinorVersion="12.0_12402" SchemaChangeCount="5" Creator="Ableton Live 12.4.5" '
                'Revision="225ce5e356e024356d5210512bae46fb466f6968">\n')
_PROTECTION = '<OverwriteProtectionNumber Value="3076" />'
_KIND_DEVICE_TYPE = {"instrument": 1, "audio_effect": 2, "midi_effect": 4}
_SCAN_TTL = 120.0
_SCAN_CACHE = {}


def is_windows():
    return sys.platform.startswith("win")


def is_macos():
    return sys.platform == "darwin"


def _env(env, name, default=None):
    value = (os.environ if env is None else env).get(name)
    return value if value else default


# ==========================================================================
# paths
# ==========================================================================

def _version_key(folder_name):
    return tuple(int(n) for n in re.findall(r"\d+", folder_name)) or (0,)


def live_preference_dirs(env=None):
    """Live's preference folders, newest version first (macOS
    ``~/Library/Preferences/Ableton/Live x.y.z``, Windows
    ``%APPDATA%\\Ableton\\Live x.y.z\\Preferences``)."""
    if is_windows():
        base = os.path.join(_env(env, "APPDATA", os.path.expanduser("~")), "Ableton")
        suffix = "Preferences"
    else:
        base = os.path.join(os.path.expanduser("~"), "Library", "Preferences", "Ableton")
        suffix = ""
    try:
        names = [n for n in os.listdir(base) if n.lower().startswith("live ")]
    except OSError:
        return []
    names.sort(key=_version_key, reverse=True)
    return [os.path.join(base, n, suffix) if suffix else os.path.join(base, n) for n in names]


def user_library_from_cfg(env=None, live_version=None):
    """The User Library path from Live's ``Library.cfg`` (``ProjectPath`` +
    ``ProjectName`` of ``<UserLibrary>``), or None."""
    import xml.etree.ElementTree as ElementTree
    folders = live_preference_dirs(env)
    if live_version:
        wanted = "live %s" % live_version
        folders.sort(key=lambda f: os.path.basename(f.rstrip("\\/")).lower() != wanted
                     and os.path.basename(os.path.dirname(f)).lower() != wanted)
    for folder in folders:
        cfg = os.path.join(folder, "Library.cfg")
        try:
            root = ElementTree.parse(cfg).getroot()
        except (OSError, ElementTree.ParseError):
            continue
        project = root.find(".//UserLibrary/LibraryProject")
        if project is None:
            continue
        path = project.find("ProjectPath")
        name = project.find("ProjectName")
        if path is not None and path.get("Value"):
            full = os.path.join(path.get("Value"), name.get("Value") if name is not None
                                and name.get("Value") else "User Library")
            if os.path.isdir(full):
                return full
    return None


def user_library(script_dir=None, env=None, live_version=None):
    """``(path, source)`` of Live's User Library: ``LIVEBRIDGE_USER_LIBRARY``, else the
    folder two levels above the Remote Script (``<User Library>/Remote Scripts/
    LiveBridge``), else ``Library.cfg``; ``(None, None)`` when nothing is found."""
    override = _env(env, "LIVEBRIDGE_USER_LIBRARY")
    if override:
        return override, "LIVEBRIDGE_USER_LIBRARY"
    script_dir = script_dir or os.path.dirname(os.path.abspath(__file__))
    remote = os.path.dirname(os.path.abspath(script_dir))
    if os.path.basename(remote).lower() == "remote scripts":
        return os.path.dirname(remote), "script folder"
    found = user_library_from_cfg(env, live_version)
    if found:
        return found, "Library.cfg"
    return None, None


def racks_dir(library):
    return os.path.join(library, "LiveBridge", "Racks")


def maps_dir(library):
    return os.path.join(library, "LiveBridge", "Maps")


def shipped_maps_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugin_maps")


# ==========================================================================
# plug-in identities
# ==========================================================================

def vst3_folders(env=None):
    """VST3 system/user folders of this platform (+ ``LIVEBRIDGE_VST3_PATH``)."""
    extra = [p for p in (_env(env, "LIVEBRIDGE_VST3_PATH", "") or "").split(os.pathsep) if p]
    if is_windows():
        common = _env(env, "COMMONPROGRAMFILES", r"C:\Program Files\Common Files")
        local = _env(env, "LOCALAPPDATA", os.path.join(os.path.expanduser("~"), "AppData",
                                                        "Local"))
        return extra + [os.path.join(common, "VST3"),
                        os.path.join(local, "Programs", "Common", "VST3")]
    return extra + ["/Library/Audio/Plug-Ins/VST3",
                    os.path.join(os.path.expanduser("~"), "Library", "Audio", "Plug-Ins", "VST3")]


def au_folders(env=None):
    """Audio Unit folders (macOS only)."""
    if not is_macos() and not _env(env, "LIVEBRIDGE_AU_PATH"):
        return []
    extra = [p for p in (_env(env, "LIVEBRIDGE_AU_PATH", "") or "").split(os.pathsep) if p]
    return extra + ["/Library/Audio/Plug-Ins/Components",
                    os.path.join(os.path.expanduser("~"), "Library", "Audio", "Plug-Ins",
                                 "Components")]


def live_database_files(env=None):
    """Live's plug-in database files (``Live-plugins-*.db`` + its WAL)."""
    if is_windows():
        base = os.path.join(_env(env, "LOCALAPPDATA", os.path.expanduser("~")), "Ableton",
                            "Live Database")
    else:
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                            "Ableton", "Live Database")
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("Live-plugins-")
                       and (n.endswith(".db") or n.endswith(".db-wal")))
    except OSError:
        return []
    return [os.path.join(base, n) for n in names]


def loads_lenient_json(text):
    """``json.loads`` that also accepts the comments and trailing commas some
    ``moduleinfo.json`` files carry (JSON5 style)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    out = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        c = text[i]
        if in_string:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_string = False
        elif c == '"':
            in_string = True
            out.append(c)
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return json.loads(re.sub(r",\s*([}\]])", r"\1", "".join(out)))


def _kind_from_categories(categories):
    text = str(categories or "").lower()
    if "instrument" in text or "synth" in text or "instr" in text:
        return "instrument"
    return "audio_effect"


def read_moduleinfo(bundle):
    """Identities of the audio classes in one ``.vst3`` bundle's moduleinfo.json."""
    for rel in (("Contents", "Resources", "moduleinfo.json"), ("Contents", "moduleinfo.json")):
        path = os.path.join(bundle, *rel)
        if os.path.isfile(path):
            break
    else:
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = loads_lenient_json(handle.read())
    except (OSError, ValueError):
        return []
    found = []
    factory = data.get("Factory Info") or {}
    for cls in data.get("Classes") or ():
        if cls.get("Category") != "Audio Module Class":
            continue
        cid = re.sub(r"[^0-9A-Fa-f]", "", str(cls.get("CID") or "")).upper()
        if len(cid) != 32:
            continue
        subs = cls.get("Sub Categories") or []
        found.append({"name": cls.get("Name") or data.get("Name"), "format": "VST3",
                      "vendor": cls.get("Vendor") or factory.get("Vendor"),
                      "version": cls.get("Version") or data.get("Version"),
                      "class_id": cid, "kind": _kind_from_categories("|".join(subs)),
                      "path": bundle, "source": "moduleinfo.json"})
    return found


def _walk_bundles(folders, suffix, max_depth=3):
    for folder in folders:
        stack = [(folder, 0)]
        while stack:
            current, depth = stack.pop()
            try:
                names = sorted(os.listdir(current))
            except OSError:
                continue
            for name in names:
                path = os.path.join(current, name)
                if name.lower().endswith(suffix):
                    yield path
                elif depth < max_depth and os.path.isdir(path):
                    stack.append((path, depth + 1))


def scan_vst3(folders=None, env=None):
    found = []
    for bundle in _walk_bundles(folders if folders is not None else vst3_folders(env), ".vst3"):
        if os.path.isdir(bundle):
            found.extend(read_moduleinfo(bundle))
    return found


def fourcc(code):
    """``"aumu"`` -> 1635085685 (big-endian 4-char code)."""
    raw = str(code).encode("latin-1")
    if len(raw) != 4:
        raise ValueError("a four-char code needs 4 characters: %r" % code)
    return struct.unpack(">I", raw)[0]


def scan_au(folders=None, env=None):
    found = []
    for bundle in _walk_bundles(folders if folders is not None else au_folders(env),
                                ".component"):
        plist_path = os.path.join(bundle, "Contents", "Info.plist")
        try:
            with open(plist_path, "rb") as handle:
                info = plistlib.load(handle)
        except Exception:
            continue
        for comp in info.get("AudioComponents") or ():
            try:
                codes = {k: str(comp[k]) for k in ("type", "subtype", "manufacturer")}
                fourcc(codes["type"])
            except (KeyError, ValueError):
                continue
            full = str(comp.get("name") or "")
            vendor, _sep, name = full.partition(": ")
            if not name:
                vendor, name = None, full
            kind = {"aumu": "instrument", "aumi": "midi_effect"}.get(codes["type"],
                                                                  "audio_effect")
            found.append({"name": name, "format": "AU", "vendor": vendor,
                          "version": info.get("CFBundleShortVersionString"), "au": codes,
                          "kind": kind, "path": bundle, "source": "Info.plist"})
    return found


def _sqlite_varint(data, pos):
    value = 0
    for i in range(9):
        byte = data[pos + i]
        if i == 8:
            return (value << 8) | byte, pos + 9
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, pos + i + 1
    return value, pos + 9


def _serial_size(serial):
    if serial >= 12:
        return (serial - 12) // 2 if serial % 2 == 0 else (serial - 13) // 2
    return (0, 1, 2, 3, 4, 6, 8, 8, 0, 0, 0, 0)[serial]


_DB_ID_RE = re.compile(rb"device:(vst3|vst):(instr|audiofx|midifx):")


def _db_record_at(data, start, columns=11):
    """Decode the SQLite record whose ``dev_identifier`` column (the 3rd of the
    ``plugins`` table) starts at ``start`` — the header is searched backwards."""
    for back in range(3, 48):
        head = start - back
        if head < 0:
            break
        try:
            size, pos = _sqlite_varint(data, head)
            if size < columns or size > 64:
                continue
            serials = []
            while pos < head + size and len(serials) < columns:
                serial, pos = _sqlite_varint(data, pos)
                serials.append(serial)
            if len(serials) != columns or pos != head + size:
                continue
            offsets, body = [], head + size
            for serial in serials:
                offsets.append(body)
                body += _serial_size(serial)
            if offsets[2] != start or serials[2] < 13 or serials[2] % 2 == 0:
                continue
            values = []
            for serial, offset in zip(serials, offsets):
                if serial >= 13 and serial % 2:
                    values.append(data[offset:offset + _serial_size(serial)].decode("utf-8"))
                else:
                    values.append(None)
            return values
        except (IndexError, UnicodeDecodeError):
            continue
    return None


def scan_live_database(paths=None, env=None):
    """Plug-ins from Live's own plug-in database (``plugins`` table rows:
    ``dev_identifier`` like ``device:vst3:instr:56534558-6673-...``, name, vendor,
    version) — read as raw bytes because Live's Python has no ``_sqlite3``."""
    found, seen = [], set()
    for path in paths if paths is not None else live_database_files(env):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            continue
        for match in _DB_ID_RE.finditer(data):
            values = _db_record_at(data, match.start())
            if not values or not values[2] or values[2] in seen:
                continue
            ident = values[2]
            seen.add(ident)
            fmt_code, kind_code, rest = ident.split(":", 3)[1:]
            kind = {"instr": "instrument", "midifx": "midi_effect"}.get(kind_code,
                                                                     "audio_effect")
            entry = {"name": values[3], "vendor": values[4], "version": values[5],
                     "kind": kind, "source": "Live plug-in database", "dev_identifier": ident}
            if fmt_code == "vst3":
                cid = re.sub(r"[^0-9A-Fa-f]", "", rest).upper()
                if len(cid) != 32:
                    continue
                entry.update({"format": "VST3", "class_id": cid})
            else:
                uid = rest.split("?", 1)[0]
                if not uid.lstrip("-").isdigit():
                    continue
                entry.update({"format": "VST2", "uid": int(uid)})
            found.append(entry)
    return found


def installed_plugins(env=None, refresh=False):
    """Every plug-in identity found on disk (VST3 moduleinfo, Live's database, AU),
    cached for ``_SCAN_TTL`` seconds."""
    cached = _SCAN_CACHE.get("all")
    if cached and not refresh and time.time() - cached[0] < _SCAN_TTL and env is None:
        return cached[1]
    found = scan_vst3(env=env)
    known = set(p["class_id"] for p in found)
    for entry in scan_live_database(env=env):
        if entry.get("class_id") not in known or entry["format"] != "VST3":
            found.append(entry)
    found.extend(scan_au(env=env))
    if env is None:
        _SCAN_CACHE["all"] = (time.time(), found)
    return found


def norm(text):
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


_FORMAT_RANK = {"VST3": 0, "AU": 1, "VST2": 2}


def find_plugin(name, plugins, fmt=None, kind=None):
    """Best identity for a plug-in name ("Serum 2", "serum2", "Xfer Records/Serum 2").

    Exact normalized name first (VST3 before AU before VST2, like Live's browser);
    then a unique prefix/substring.  Raises ``LookupError`` with the candidates."""
    text = str(name or "")
    vendor = None
    if "/" in text:
        vendor, text = text.rsplit("/", 1)
    wanted = norm(text)
    if not wanted:
        raise LookupError("empty plug-in name")
    pool = [p for p in plugins if (fmt is None or p.get("format") == fmt)
            and (kind is None or p.get("kind") == kind)
            and (vendor is None or norm(vendor) in norm(p.get("vendor")))]
    for tier in ([p for p in pool if norm(p.get("name")) == wanted],
                 [p for p in pool if norm(p.get("name")).startswith(wanted)],
                 [p for p in pool if wanted in norm(p.get("name"))]):
        if not tier:
            continue
        names = sorted(set(norm(p.get("name")) for p in tier))
        if len(names) > 1:
            raise LookupError("plug-in %r is ambiguous: %s" % (
                name, ", ".join(sorted(set(str(p.get("name")) for p in tier))[:10])))
        tier.sort(key=lambda p: (_FORMAT_RANK.get(p.get("format"), 9),
                                 0 if p.get("source") == "moduleinfo.json" else 1))
        return tier[0]
    raise LookupError("no installed plug-in named %r%s (%d known)" % (
        name, " in format %s" % fmt if fmt else "", len(pool)))


def class_id_fields(class_id):
    """VST3 class id (32 hex digits) -> Live's ``<Uid>`` Fields.0..3: four
    big-endian int32 (signed, as Live stores ints)."""
    raw = bytes.fromhex(re.sub(r"[^0-9A-Fa-f]", "", class_id))
    if len(raw) != 16:
        raise ValueError("a VST3 class id has 32 hex digits: %r" % class_id)
    return list(struct.unpack(">4i", raw))


def map_key(identity):
    """File-name key of a plug-in's parameter map: ``serum2_vst3``."""
    return "%s_%s" % (norm(identity.get("name")) or "plugin",
                      str(identity.get("format") or "x").lower())


# ==========================================================================
# plug-in state (.vstpreset, Serum 2 presets, .aupreset)
# ==========================================================================

def parse_vstpreset(data):
    """``{"class_id", "chunks": {"Comp": bytes, "Cont": bytes, "Info": bytes ...}}``
    from a VST3 ``.vstpreset`` ('VST3' header, class id, chunk list 'List')."""
    if len(data) < 48 or data[:4] != b"VST3":
        raise ValueError("not a .vstpreset file (no 'VST3' header)")
    class_id = data[8:40].decode("ascii", "replace").upper()
    offset = struct.unpack("<q", data[40:48])[0]
    if offset <= 0 or offset + 8 > len(data) or data[offset:offset + 4] != b"List":
        raise ValueError(".vstpreset has no chunk list")
    count = struct.unpack("<i", data[offset + 4:offset + 8])[0]
    chunks = {}
    for i in range(max(0, count)):
        entry = data[offset + 8 + i * 20:offset + 28 + i * 20]
        if len(entry) < 20:
            break
        chunk_id = entry[:4].decode("ascii", "replace")
        start, size = struct.unpack("<qq", entry[4:])
        chunks[chunk_id] = data[start:start + size]
    return {"class_id": class_id, "chunks": chunks}


def build_vstpreset(class_id, chunks):
    """Inverse of :func:`parse_vstpreset` (chunks in the given order)."""
    body = b""
    table = []
    start = 48
    for chunk_id, payload in chunks.items():
        table.append((chunk_id, start + len(body), len(payload)))
        body += payload
    list_offset = 48 + len(body)
    data = b"VST3" + struct.pack("<i", 1) + class_id.upper().encode("ascii") + \
        struct.pack("<q", list_offset) + body + b"List" + struct.pack("<i", len(table))
    for chunk_id, start, size in table:
        data += chunk_id.encode("ascii")[:4] + struct.pack("<qq", start, size)
    return data


def parse_xfer(data):
    """Xfer's container (Serum 2 states and ``.SerumPreset`` files): ``XferJson\\0``,
    u64 JSON length, JSON header, then u32 raw size, u32 format, zstd frame.
    Returns ``(header dict, raw_size, format, frame)``."""
    if data[:9] != b"XferJson\x00":
        raise ValueError("not an Xfer container")
    size = struct.unpack("<Q", data[9:17])[0]
    header = json.loads(data[17:17 + size].decode("utf-8"))
    rest = data[17 + size:]
    raw_size, fmt = struct.unpack("<II", rest[:8])
    return header, raw_size, fmt, rest[8:]


def build_xfer(header, raw_size, fmt, frame):
    """Inverse of :func:`parse_xfer`; ``header["hash"]`` is the MD5 of the frame."""
    header = dict(header)
    header["hash"] = hashlib.md5(frame).hexdigest()
    text = json.dumps(_json_float(header), separators=(",", ":"), sort_keys=True,
                      ensure_ascii=False)
    blob = text.encode("utf-8")
    return b"XferJson\x00" + struct.pack("<Q", len(blob)) + blob + \
        struct.pack("<II", raw_size, fmt) + frame


def _json_float(header):
    """Keep ``version`` a float (Xfer writes ``11.0``)."""
    if isinstance(header.get("version"), int):
        header["version"] = float(header["version"])
    return header


def read_state_file(path, identity):
    """Plug-in state from a preset file for ``identity``.

    Returns ``{"processor"?, "controller"?, "au_buffer"?, "kind", "preset_name"}``;
    raises ``ValueError`` for a file that does not fit the plug-in."""
    with open(path, "rb") as handle:
        data = handle.read()
    name = os.path.splitext(os.path.basename(path))[0]
    fmt = identity.get("format")
    if data[:4] == b"VST3":
        if fmt != "VST3":
            raise ValueError("%s is a VST3 preset but the plug-in is %s" % (path, fmt))
        preset = parse_vstpreset(data)
        wanted = str(identity.get("class_id") or "").upper()
        if wanted and preset["class_id"] != wanted:
            raise ValueError("%s belongs to another plug-in (class id %s, expected %s)"
                             % (os.path.basename(path), preset["class_id"], wanted))
        chunks = preset["chunks"]
        if "Comp" not in chunks:
            raise ValueError("%s has no processor state ('Comp' chunk)" % path)
        return {"processor": chunks["Comp"], "controller": chunks.get("Cont"),
                "kind": "vstpreset", "preset_name": name}
    if data[:9] == b"XferJson\x00":
        raise ValueError(SERUM_PRESET_REFUSED)
    if path.lower().endswith(".aupreset"):
        if fmt != "AU":
            raise ValueError(".aupreset files only fit the Audio Unit version")
        info = plistlib.loads(data)
        return {"au_buffer": plistlib.dumps(info, fmt=plistlib.FMT_XML), "kind": "aupreset",
                "preset_name": str(info.get("name") or name)}
    raise ValueError("unsupported preset file %s (use .vstpreset, .SerumPreset or .aupreset)"
                     % os.path.basename(path))


def state_from_live_preset(xml, identity):
    """The plug-in state stored in a Live preset/set XML (``.adv`` / ``.adg`` /
    ``.als``): the first ``<Vst3Preset>`` whose ``<Uid>`` is the plug-in's class id
    (``{"processor", "controller"}``) or the first ``<AuPreset>`` ``<Buffer>``."""
    if identity.get("format") == "VST3":
        fields = class_id_fields(identity["class_id"])
        for block in re.findall(r"<Vst3Preset\b.*?</Vst3Preset>", xml, re.S):
            uid = [int(v) for v in re.findall(r'<Fields\.\d Value="(-?\d+)"', block)[:4]]
            if uid != fields:
                continue
            proc = re.search(r"<ProcessorState>(.*?)</ProcessorState>", block, re.S)
            cont = re.search(r"<ControllerState>(.*?)</ControllerState>", block, re.S)
            processor = bytes.fromhex("".join(proc.group(1).split())) if proc else b""
            controller = bytes.fromhex("".join(cont.group(1).split())) if cont else b""
            if processor:
                return {"processor": processor, "controller": controller or None}
        return None
    if identity.get("format") == "AU":
        match = re.search(r"<AuPreset\b.*?<Buffer>(.*?)</Buffer>", xml, re.S)
        if match and match.group(1).strip():
            return {"au_buffer": bytes.fromhex("".join(match.group(1).split()))}
    return None


def load_state(path, identity):
    """Plug-in state from ``path``: a ``.vstpreset`` (VST3), a Live preset
    (``.adv`` / ``.adg``) that holds the plug-in, or an ``.aupreset`` (AU).

    Returns ``{"processor"?, "controller"?, "au_buffer"?, "kind", "preset_name"}``;
    raises ``ValueError`` (unsupported file, other plug-in) or ``OSError``."""
    lowered = path.lower()
    name = os.path.splitext(os.path.basename(path))[0]
    if lowered.endswith((".adv", ".adg", ".als")):
        state = state_from_live_preset(read_rack(path), identity)
        if not state:
            raise ValueError("%s does not contain %s (%s)" % (os.path.basename(path),
                                                              identity.get("name"),
                                                              identity.get("format")))
        state.update({"kind": "live_preset", "preset_name": name})
        return state
    if lowered.endswith(".serumpreset"):
        raise ValueError(SERUM_PRESET_REFUSED)
    return read_state_file(path, identity)


def _expand(path, env=None):
    env = os.environ if env is None else env
    text = os.path.expanduser(path)

    def repl(match):
        return env.get(match.group(1)) or match.group(0)
    return re.sub(r"%([A-Za-z0-9_]+)%", repl, text)


def preset_folders(identity, library=None, extra=None, env=None):
    """Folders to look for preset files of ``identity``: the plug-in's own folders
    from its map (``extra`` = ``{"macos": [...], "windows": [...]}``), the VST3 /
    AU preset locations, the Splice presets folder and the User Library."""
    name = str(identity.get("name") or "")
    vendor = str(identity.get("vendor") or "")
    folders = []
    platform_key = "windows" if is_windows() else "macos"
    for path in (extra or {}).get(platform_key, []):
        folders.append(_expand(path, env))
    if is_windows():
        profile = _env(env, "USERPROFILE", os.path.expanduser("~"))
        for base in (os.path.join(profile, "Documents", "VST3 Presets"),
                     os.path.join(_env(env, "APPDATA", profile), "VST3 Presets"),
                     os.path.join(_env(env, "PROGRAMDATA", r"C:\ProgramData"), "VST3 Presets")):
            folders.append(os.path.join(base, vendor, name))
        folders.append(os.path.join(profile, "Splice", "presets"))
        folders.append(os.path.join(profile, "Documents", "Splice", "presets"))
    else:
        home = os.path.expanduser("~")
        for base in (os.path.join(home, "Library", "Audio", "Presets"), "/Library/Audio/Presets"):
            folders.append(os.path.join(base, vendor, name))
        folders.append(os.path.join(home, "Splice", "presets"))
    if library:
        folders.append(library)
    out = []
    for folder in folders:
        if folder and folder not in out:
            out.append(folder)
    return out


PRESET_EXTENSIONS = (".vstpreset", ".serumpreset", ".adv", ".adg", ".aupreset")


def _preset_entry(path, identity, fields_text):
    lowered = path.lower()
    fmt = identity.get("format")
    entry = {"name": os.path.splitext(os.path.basename(path))[0], "path": path}
    if lowered.endswith(".vstpreset"):
        try:
            with open(path, "rb") as handle:
                head = handle.read(48)
        except OSError:
            return None
        if head[:4] != b"VST3" or fmt != "VST3" or \
                head[8:40].decode("ascii", "replace").upper() != identity.get("class_id"):
            return None
        entry.update({"kind": "vstpreset", "embeddable": True})
    elif lowered.endswith(".serumpreset"):
        if norm(identity.get("name")) != "serum2":
            return None
        entry.update({"kind": "SerumPreset", "embeddable": False})
    elif lowered.endswith(".aupreset"):
        if fmt != "AU":
            return None
        entry.update({"kind": "aupreset", "embeddable": True})
    else:   # .adv / .adg: only Live presets that hold this plug-in
        try:
            if os.path.getsize(path) > 8 * 1024 * 1024:
                return None
            xml = read_rack(path)
        except (OSError, EOFError, UnicodeDecodeError, ValueError):
            return None
        if fields_text is None or fields_text not in xml:
            return None
        entry.update({"kind": "live_preset", "embeddable": True})
    return entry


def list_presets(folders, identity, max_files=30000, max_live_presets=600):
    """Preset files for ``identity`` under ``folders`` (bounded walk).

    Returns ``(entries, truncated)``; each entry ``{name, path, kind, embeddable,
    folder}``.  ``.adv`` / ``.adg`` files count only when they contain the plug-in
    (at most ``max_live_presets`` are opened)."""
    fields_text = None
    if identity.get("format") == "VST3" and identity.get("class_id"):
        fields_text = '<Fields.0 Value="%d" />' % class_id_fields(identity["class_id"])[0]
    elif identity.get("format") == "AU":
        fields_text = '<SubType Value="%d" />' % fourcc(identity["au"]["subtype"])
    entries, seen = [], set()
    visited = 0
    opened = [0]
    truncated = False
    for folder in folders:
        for base, dirs, files in os.walk(folder):
            # skip LiveBridge's own generated racks / maps and bundles
            dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                             and d not in ("Remote Scripts", "LiveBridge")
                             and not d.endswith((".vst3", ".component", ".app")))
            for fname in sorted(files):
                visited += 1
                if visited > max_files:
                    return entries, True
                lowered = fname.lower()
                if not lowered.endswith(PRESET_EXTENSIONS) or fname.startswith("LB_"):
                    continue
                path = os.path.join(base, fname)
                if path in seen:
                    continue
                seen.add(path)
                if lowered.endswith((".adv", ".adg")):
                    if opened[0] >= max_live_presets:
                        truncated = True
                        continue
                    opened[0] += 1
                entry = _preset_entry(path, identity, fields_text)
                if entry:
                    entry["folder"] = folder
                    rel = os.path.relpath(base, folder)
                    if rel != ".":
                        entry["category"] = rel.replace(os.sep, "/")
                    entries.append(entry)
    return entries, truncated


# ==========================================================================
# rack XML
# ==========================================================================

def quoteattr(text):
    """XML attribute value in double quotes (``&``, ``<``, ``>``, ``"`` escaped)."""
    return '"%s"' % (str(text).replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))


def _v(tag, value):
    if isinstance(value, bool):
        value = "true" if value else "false"
    return "<%s Value=%s />" % (tag, quoteattr(str(value)))


def _range(tag, low, high):
    return "<%s><Min Value=\"%s\" /><Max Value=\"%s\" /></%s>" % (tag, low, high, tag)


def _automation_block(tag, manual, low="0", high="127", modulation=True):
    parts = ["<%s>" % tag, '<LomId Value="0" />', _v("Manual", manual)]
    parts.append(_range("MidiControllerRange", low, high))
    parts.append('<AutomationTarget Id="0"><LockEnvelope Value="0" /></AutomationTarget>')
    if modulation:
        parts.append('<ModulationTarget Id="0"><LockEnvelope Value="0" /></ModulationTarget>')
    parts.append("</%s>" % tag)
    return "".join(parts)


def _on_block():
    return ('<On><LomId Value="0" /><Manual Value="true" /><AutomationTarget Id="0">'
            '<LockEnvelope Value="0" /></AutomationTarget><MidiCCOnOffThresholds>'
            '<Min Value="64" /><Max Value="127" /></MidiCCOnOffThresholds></On>')


def _default_preset_ref(device_id):
    return ('<PresetRef><AbletonDefaultPresetRef Id="0"><FileRef><RelativePathType Value="0" />'
            '<RelativePath Value="" /><Path Value="" /><Type Value="2" />'
            '<LivePackName Value="" /><LivePackId Value="" /><OriginalFileSize Value="0" />'
            '<OriginalCrc Value="0" /><SourceHint Value="" /></FileRef><DeviceId Name="%s" />'
            '</AbletonDefaultPresetRef></PresetRef>' % device_id)


def _device_head(user_name=""):
    return ('<LomId Value="0" /><LomIdView Value="0" /><IsExpanded Value="true" />'
            '<BreakoutIsExpanded Value="false" />' + _on_block() +
            '<ModulationSourceCount Value="0" /><ParametersListWrapper LomId="0" />'
            '<Pointee Id="0" /><LastSelectedTimeableIndex Value="0" />'
            '<LastSelectedClipEnvelopeIndex Value="0" /><LastPresetRef><Value /></LastPresetRef>'
            '<LockedScripts /><IsFolded Value="false" /><ShouldShowPresetName Value="true" />'
            + _v("UserName", user_name) + '<Annotation Value="" /><SourceContext><Value />'
            '</SourceContext><MpePitchBendUsesTuning Value="true" /><ViewData Value="{}" />'
            + _PROTECTION)


def hex_block(data, width=80):
    text = data.hex().upper()
    return "\n".join(text[i:i + width] for i in range(0, len(text), width))


def _parameter_settings(parameters):
    rows = []
    for index, param in enumerate(parameters):
        macro = int(param.get("macro", -1))
        mapped = macro >= 0
        rows.append(
            '<PluginParameterSettings Id="%d">%s%s%s<Type Value="PluginFloatParameter" />%s%s'
            '<LomId Value="0" /></PluginParameterSettings>'
            % (index, _v("Index", index), _v("VisualIndex", index),
               _v("ParameterId", int(param["id"])), _v("MacroControlIndex", macro),
               '<MidiControllerRange><MidiControllerRange Id="0"><Min Value="%s" />'
               '<Max Value="%s" /></MidiControllerRange></MidiControllerRange>'
               % (param.get("min", MACRO_RANGE[0]), param.get("max", MACRO_RANGE[1]))
               if mapped else "<MidiControllerRange />"))
    return "<ParameterSettings>%s</ParameterSettings>" % "".join(rows)


def _plugin_common(parameters):
    return (_PROTECTION + _parameter_settings(parameters) +
            '<IsOn Value="true" /><PowerMacroControlIndex Value="-1" />'
            + _range("PowerMacroMappingRange", 64, 127) +
            '<IsFolded Value="false" /><StoredAllParameters Value="true" />'
            '<DeviceLomId Value="0" /><DeviceViewLomId Value="0" /><IsOnLomId Value="0" />'
            '<ParametersListWrapperLomId Value="0" />')


def plugin_preset_xml(identity, parameters, processor=None, controller=None, au_buffer=None):
    """The ``<Vst3Preset>`` / ``<AuPreset>`` element for the rack's chain."""
    fmt = identity.get("format")
    kind = identity.get("kind") or "instrument"
    if fmt == "VST3":
        fields = class_id_fields(identity["class_id"])
        uid = "<Uid>%s</Uid>" % "".join(_v("Fields.%d" % i, f) for i, f in enumerate(fields))
        return ('<Vst3Preset Id="0">%s<MpeEnabled Value="0" /><MpeSettings>'
                '<ZoneType Value="0" /><FirstNoteChannel Value="1" />'
                '<LastNoteChannel Value="15" /></MpeSettings>' % _PROTECTION
                ) + _plugin_common(parameters)[len(_PROTECTION):] + uid + \
            _v("DeviceType", _KIND_DEVICE_TYPE.get(kind, 1)) + \
            "<ProcessorState>%s</ProcessorState>" % (hex_block(processor) if processor else "") + \
            "<ControllerState>%s</ControllerState>" % (hex_block(controller) if controller
                                                        else "") + \
            _v("Name", identity.get("name") or "") + "<PresetRef /></Vst3Preset>"
    if fmt == "AU":
        codes = identity["au"]
        return ('<AuPreset Id="0">' + _plugin_common(parameters) +
                "<Buffer>%s</Buffer>" % (hex_block(au_buffer) if au_buffer else "") +
                "<PresetRef />" + _v("Name", "") +
                _v("Manufacturer", fourcc(codes["manufacturer"])) +
                _v("SubType", fourcc(codes["subtype"])) + _v("Type", fourcc(codes["type"])) +
                "</AuPreset>")
    raise ValueError("generated racks support VST3 and Audio Unit plug-ins, not %s" % fmt)


def _macro_section(macro_names, macro_values):
    parts = []
    for i in range(MAX_MACROS):
        parts.append(_automation_block("MacroControls.%d" % i, macro_values.get(i, 0)))
    for i in range(MAX_MACROS):
        parts.append(_v("MacroDisplayNames.%d" % i, macro_names.get(i, "Macro %d" % (i + 1))))
    for i in range(MAX_MACROS):
        parts.append(_v("MacroDefaults.%d" % i, -1))
    for i in range(MAX_MACROS):
        parts.append(_v("MacroAnnotations.%d" % i, ""))
    for i in range(MAX_MACROS):
        parts.append(_v("ForceDisplayGenericValue.%d" % i, False))
    return "".join(parts)


def _macro_tail():
    parts = []
    for name in ("MacroColor.%d",):
        parts.extend(_v(name % i, -1) for i in range(MAX_MACROS))
    parts.append('<LockId Value="0" /><LockSeal Value="0" /><ChainsListWrapper LomId="0" />'
                 '<ReturnChainsListWrapper LomId="0" /><MacroVariations><MacroSnapshots />'
                 '</MacroVariations>')
    parts.extend(_v("ExcludeMacroFromRandomization.%d" % i, False) for i in range(MAX_MACROS))
    parts.extend(_v("ExcludeMacroFromSnapshots.%d" % i, False) for i in range(MAX_MACROS))
    parts.append('<AreMacroVariationsControlsVisible Value="false" />'
                 '<ChainSelectorFilterMidiCtrl Value="false" /><RangeTypeIndex Value="1" />'
                 '<ShowsZonesInsteadOfNoteNames Value="false" />')
    return "".join(parts)


def _mixer_preset():
    return ('<MixerPreset><AbletonDevicePreset Id="0">' + _PROTECTION +
            '<Device><AudioBranchMixerDevice Id="0">' + _device_head() +
            '<Speaker><LomId Value="0" /><Manual Value="true" /><AutomationTarget Id="0">'
            '<LockEnvelope Value="0" /></AutomationTarget><MidiCCOnOffThresholds>'
            '<Min Value="64" /><Max Value="127" /></MidiCCOnOffThresholds></Speaker>'
            + _automation_block("Volume", 1, "0.0003162277571", "1.99526238")
            + _automation_block("Panorama", 0, "-1", "1") +
            '<SendInfos /><RoutingHelper><Routable><Target Value="AudioOut/None" />'
            '<UpperDisplayString Value="No Output" /><LowerDisplayString Value="" />'
            '<MpeSettings><ZoneType Value="0" /><FirstNoteChannel Value="1" />'
            '<LastNoteChannel Value="15" /></MpeSettings>'
            '<MpePitchBendUsesTuning Value="true" /></Routable><TargetEnum Value="0" />'
            '</RoutingHelper><SendsListWrapper LomId="0" /></AudioBranchMixerDevice></Device>'
            + _default_preset_ref("AudioBranchMixerDevice") +
            '</AbletonDevicePreset></MixerPreset>')


def rack_xml(identity, parameters, macro_names=None, macro_values=None, processor=None,
             controller=None, au_buffer=None, rack_name="", chain_name=""):
    """The complete rack preset XML (an ``.adg`` before gzip).

    Args:
        identity: plug-in identity (``format`` VST3/AU, ``class_id`` / ``au``,
            ``kind`` instrument / audio_effect, ``name``).
        parameters: ``[{"id": ParameterId, "macro": -1 | 0..15, "min"?, "max"?}]``,
            at most :data:`MAX_PARAMETERS`, in the order Live should list them;
            ``min`` / ``max`` (plug-in value units) default to :data:`MACRO_RANGE`.
        macro_names / macro_values: ``{macro index: name / 0..127}``.
        processor, controller, au_buffer: plug-in state bytes (None = the
            plug-in's default state).
        rack_name: the rack's name in Live (``UserName``; empty = file name).
    """
    if len(parameters) > MAX_PARAMETERS:     # more crashed Live 12.4.5
        raise ValueError("at most %d parameters per plug-in (got %d)"
                         % (MAX_PARAMETERS, len(parameters)))
    for param in parameters:
        macro = int(param.get("macro", -1))
        if not -1 <= macro < MAX_MACROS:
            raise ValueError("macro index must be -1 or 0..%d, got %d" % (MAX_MACROS - 1, macro))
    kind = identity.get("kind") or "instrument"
    if kind == "instrument":
        group, branch = "InstrumentGroupDevice", "InstrumentBranchPreset"
    elif kind == "audio_effect":
        group, branch = "AudioEffectGroupDevice", "AudioEffectBranchPreset"
    else:
        raise ValueError("generated racks support instruments and audio effects, not %s"
                         % kind)
    macro_names = dict(macro_names or {})
    macro_values = dict(macro_values or {})
    visible = 16 if any(i >= 8 for i in list(macro_names) + list(macro_values)) else 8
    device = (('<%s Id="0">' % group) + _device_head(rack_name) +
              '<Branches /><IsBranchesListVisible Value="false" />'
              '<IsReturnBranchesListVisible Value="false" />'
              '<IsRangesEditorVisible Value="false" /><AreDevicesVisible Value="true" />'
              + _v("NumVisibleMacroControls", visible)
              + _macro_section(macro_names, macro_values)
              + _v("AreMacroControlsVisible", bool(macro_names))
              + '<IsAutoSelectEnabled Value="false" />'
              + _automation_block("ChainSelector", 0)
              + '<ChainSelectorRelativePosition Value="-1073741824" />'
              '<ViewsToRestoreWhenUnfolding Value="0" /><ReturnBranches />'
              '<BranchesSplitterProportion Value="0.5" />'
              '<ShowBranchesInSessionMixer Value="false" />'
              + _macro_tail() + ("</%s>" % group))
    zone = ""
    if kind == "instrument":
        zone = ('<ZoneSettings><KeyRange><Min Value="0" /><Max Value="127" />'
                '<CrossfadeMin Value="0" /><CrossfadeMax Value="127" /></KeyRange>'
                '<VelocityRange><Min Value="1" /><Max Value="127" /><CrossfadeMin Value="1" />'
                '<CrossfadeMax Value="127" /></VelocityRange></ZoneSettings>')
    branch_xml = (('<%s Id="0">' % branch) + _v("Name", chain_name) +
                  '<IsSoloed Value="false" /><DevicePresets>' +
                  plugin_preset_xml(identity, parameters, processor, controller, au_buffer) +
                  '</DevicePresets>' + _mixer_preset() +
                  '<BranchSelectorRange><Min Value="0" /><Max Value="0" />'
                  '<CrossfadeMin Value="0" /><CrossfadeMax Value="0" /></BranchSelectorRange>'
                  '<SessionViewBranchWidth Value="55" /><DocumentColorIndex Value="22" />'
                  '<AutoColored Value="true" /><AutoColorScheme Value="0" /><SourceContext />'
                  + zone + ("</%s>" % branch))
    return (_LIVE_HEADER + "<GroupDevicePreset>" + _PROTECTION + "<Device>" + device +
            "</Device>" + _default_preset_ref(group) + "<BranchPresets>" + branch_xml +
            "</BranchPresets><ReturnBranchPresets /></GroupDevicePreset>\n</Ableton>\n")


def write_rack(path, xml):
    """Write ``xml`` gzip-compressed (Live's ``.adg`` format) atomically; returns
    True when the file was (re)written, False when an identical file exists."""
    payload = xml.encode("utf-8")
    try:
        with gzip.open(path, "rb") as handle:
            if handle.read() == payload:
                return False
    except (OSError, EOFError):
        pass
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + ".tmp"
    with gzip.open(temp, "wb") as handle:
        handle.write(payload)
    os.replace(temp, path)
    return True


def read_rack(path):
    """The XML of a (gzip or plain) rack preset file."""
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8")


# ==========================================================================
# parameter maps
# ==========================================================================

def load_map(path):
    """A parameter map JSON (``{"plugin", "format", "parameters": [[name, id], ...],
    "groups"?: {...}, ...}``) or None."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("parameters"), list):
        return None
    return data


def save_map(path, data):
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    os.replace(temp, path)


def is_valid_name(name):
    return bool(name) and not INVALID_NAME_RE.match(str(name))


class Prober(object):
    """Finds a plug-in's ParameterIds by probing — the pure logic, no Live.

    Loop: ``ids = prober.next_ids()`` (at most 128 candidate ids; ``[]`` when
    finished), load a rack exposing exactly those ids, then
    ``prober.record(ids, names)`` with the names Live shows for them (Live names
    an id the plug-in does not know "Parameter #n").

    Id layout it looks for (verified with Serum 2 VST3): ``block * 1_000_000 +
    instance * 1000 + index`` — Serum's oscillator A is 1000000.., B 1001000..,
    Noise 1003000.., Filter 1 2000000.., Env 2 3001000.., Macro 8 7007000.. .
    Plain plug-ins number their parameters 0..N, which is block 0, instance 0.

    1. blocks: ids ``b * 1_000_000 + 0..3`` for 32 blocks (32 more while the
       highest block probed still answers);
    2. instances: for every block with a hit, ``base + k * 1000`` for k = 1..16
       (16 more while the highest instance probed still answers);
    3. regions: every (block, instance) with a hit is scanned upwards from index
       0 until ``gap`` consecutive ids are unknown.
    """

    STRIDE = 1000

    def __init__(self, gap=16, max_batches=120, batch_size=MAX_PARAMETERS):
        self.gap = int(gap)
        self.max_batches = int(max_batches)
        self.batch_size = int(batch_size)
        self.found = {}
        self.probed = set()
        self.batches = 0
        self._blocks_to = 0              # blocks probed: 0 .. _blocks_to - 1
        self._instances_to = {}          # block -> instances probed: 1 .. value - 1
        self._regions = {}               # base -> next index to probe

    # -- planning ------------------------------------------------------------
    def _block_hits(self):
        return sorted(set(pid // BLOCK for pid in self.found))

    def _instances(self, block):
        return set((pid % BLOCK) // self.STRIDE for pid in self.found if pid // BLOCK == block)

    def _candidates(self):
        """``(ids still worth probing in priority order, stage)``; stage is
        ``("blocks", n)`` / ``("instances", {block: n})`` / ``("regions",)`` /
        ``("siblings",)`` — the planner's bookkeeping for :meth:`next_ids`."""
        hits = self._block_hits()
        top = self._blocks_to - 1
        if self._blocks_to == 0 or (top in hits and self._blocks_to < 4096):
            first = self._blocks_to
            return ([b * BLOCK + i for b in range(first, first + 32) for i in range(4)],
                    ("blocks", first + 32))
        wanted, marks = [], {}
        for block in hits:
            upto = self._instances_to.get(block, 1)
            instances = self._instances(block)
            if (upto == 1 or (upto - 1) in instances or (upto - 2) in instances) \
                    and upto * self.STRIDE < BLOCK:
                end = min(upto + 16, BLOCK // self.STRIDE)
                wanted.extend(block * BLOCK + k * self.STRIDE + i
                              for k in range(upto, end) for i in range(3))
                marks[block] = end
        if marks:
            return wanted, ("instances", marks)
        for base in sorted(self._regions):
            nxt = self._regions[base]
            last = max([pid - base for pid in self.found if base <= pid < base + self.STRIDE]
                       or [-1])
            if nxt - last > self.gap or nxt >= self.STRIDE:
                continue
            wanted.extend(base + i for i in range(nxt, min(self.STRIDE, last + self.gap + 1)))
        if wanted:
            return wanted, ("regions",)
        # siblings share a layout (Serum's Noise/Sub use the oscillators' indices):
        # probe every instance of a block at every index any instance answered
        for block in hits:
            layout = sorted(set(pid % self.STRIDE for pid in self.found if pid // BLOCK == block))
            for instance in sorted(self._instances(block)):
                base = block * BLOCK + instance * self.STRIDE
                wanted.extend(base + i for i in layout if base + i not in self.probed)
        return wanted, ("siblings",)

    def next_ids(self):
        """The next batch of candidate ids (``[]`` = finished)."""
        if self.batches >= self.max_batches:
            return []
        for _attempt in range(64):
            wanted, stage = self._candidates()
            batch = []
            seen = set()
            for pid in wanted:
                if pid not in self.probed and pid not in seen:
                    batch.append(pid)
                    seen.add(pid)
                if len(batch) >= self.batch_size:
                    break
            complete = len(batch) < self.batch_size or batch[-1] == wanted[-1]
            if stage[0] == "blocks":
                self._blocks_to = stage[1]
            elif stage[0] == "instances" and complete:
                self._instances_to.update(stage[1])
            if batch or stage[0] in ("regions", "siblings"):
                break
        if batch:
            self.batches += 1
        return batch

    # -- results -------------------------------------------------------------
    def record(self, ids, names):
        """Store what Live named each probed id; opens a region for every hit."""
        for pid, name in zip(ids, names):
            self.probed.add(int(pid))
            if is_valid_name(name):
                self.found[int(pid)] = str(name)
                base = int(pid) - int(pid) % self.STRIDE
                self._regions.setdefault(base, 0)
        for base in self._regions:
            nxt = self._regions[base]
            while base + nxt in self.probed and nxt < self.STRIDE:
                nxt += 1
            self._regions[base] = nxt

    def done(self):
        return self.batches >= self.max_batches or not self._candidates()[0]

    def parameters(self):
        """``[[name, id], ...]`` sorted by id."""
        return [[name, pid] for pid, name in sorted(self.found.items())]


def missing_names(expected, mapped, skip=MIDI_PROXY_RE):
    """Names of ``expected`` (``get_parameter_names()``) not in ``mapped``.

    Duplicate names count per occurrence.  MIDI proxies ("CC7 Chan 1", "Pitch
    Bend Chan 2") are skipped, and so are names after the first proxy that
    repeat a mapped name (Serum 2 lists "Mod Wheel" / "Pitch Bend" again as
    VST3 MIDI-mapping proxies at the very end)."""
    have = {}
    for name in mapped:
        have[name] = have.get(name, 0) + 1
    known = set(have)
    out = []
    after_proxies = False
    for name in expected or ():
        if skip is not None and skip.match(name):
            after_proxies = True
            continue
        if have.get(name):
            have[name] -= 1
        elif not (after_proxies and name in known):
            out.append(name)
    return out
