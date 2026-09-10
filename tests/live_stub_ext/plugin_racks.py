"""Stub extension for ``plugin_racks``: Live's browser indexing of generated rack files and
loading them the way Live 12.4.5 does (measured 2026-09-10 with Serum 2 VST3 on macOS).

What real Live did, and what this stub mirrors:

* Live's browser lists ``<User Library>/LiveBridge/Racks/<file>.adg`` only after its indexer
  saw the file (~3 s).  Here a new file shows up on the ``index_after``-th listing of the
  Racks folder (default 2: the first listing after writing misses it).  A file Live already
  indexed can be rewritten and loaded again at once — Live reads the file when loading.
* ``browser.load_item`` of such a rack creates an Instrument Rack (effects: Audio Effect
  Rack) named after the file, with one chain holding the plug-in.  The plug-in's
  ``parameters`` are "Device On" + one parameter per ``<PluginParameterSettings>`` in file
  order, named from the plug-in's ParameterId table; unknown ids become
  ``"Parameter #<slot>"`` (slot = 1-based position), like Live.
* More than 128 ``PluginParameterSettings`` crashed Live, and so did a macro index with an
  empty ``<MidiControllerRange />`` — the stub raises ``LiveWouldCrash`` so a regression
  fails loudly.
* A wired macro (``MacroControlIndex`` + range -1e9..1e9) drives its parameter linearly:
  macro/127 == parameter value (Live applies it on the next tick; the stub at once).
* ``get_parameter_names()`` of the loaded plug-in is its full name list.

``install(Live)`` returns a namespace with ``setup(browser, library, plugins, index_after=2)``
(``plugins``: ``{class_id: {"name", "names": [...], "ids": {id: name}, "formatters": {name: f}}}``)
and ``LiveWouldCrash``.  ``setup`` returns a controller with ``loads`` (parsed rack dicts) and
``listings``.
"""

import gzip
import os
import re
import struct
import types
import xml.etree.ElementTree as ElementTree

_CACHE = {}


class LiveWouldCrash(RuntimeError):
    """Real Live 12.4.5 crashed on this rack file."""


def _parse_rack(path):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    root = ElementTree.fromstring(data.decode("utf-8"))
    device = root.find("GroupDevicePreset/Device")
    group = list(device)[0]
    macro_names = {}
    for i in range(16):
        node = group.find("MacroDisplayNames.%d" % i)
        if node is not None:
            macro_names[i] = node.get("Value")
    preset = root.find(".//DevicePresets/Vst3Preset")
    au = root.find(".//DevicePresets/AuPreset")
    plugin = preset if preset is not None else au
    settings = []
    for node in plugin.findall("ParameterSettings/PluginParameterSettings"):
        rng = node.find("MidiControllerRange")
        slot = rng.find("MidiControllerRange") if rng is not None else None
        settings.append({
            "id": int(node.find("ParameterId").get("Value")),
            "macro": int(node.find("MacroControlIndex").get("Value")),
            "range": None if slot is None else (float(slot.find("Min").get("Value")),
                                                float(slot.find("Max").get("Value"))),
        })
    info = {"group": group.tag, "user_name": group.find("UserName").get("Value"),
            "macro_names": macro_names, "settings": settings, "format": plugin.tag}
    if preset is not None:
        fields = [int(preset.find("Uid/Fields.%d" % i).get("Value")) for i in range(4)]
        info["class_id"] = struct.pack(">4i", *fields).hex().upper()
        info["processor"] = "".join((preset.find("ProcessorState").text or "").split())
        info["controller"] = "".join((preset.find("ControllerState").text or "").split())
    return info


def install(Live):
    """Build the helper namespace (idempotent)."""
    if "ns" in _CACHE:
        return _CACHE["ns"]
    from live_stub import factory
    from live_stub_ext import plugins_live
    ext = plugins_live.install(Live)
    model = Live._model
    BrowserItem = model.BrowserItem
    RackDevice = model.RackDevice

    class DynamicFolder(BrowserItem):
        """A browser folder whose children are computed on every access."""

        def __init__(self, name, uri, lister):
            BrowserItem.__init__(self, name, uri=uri, is_folder=True, source="User")
            self._lister = lister

        @property
        def children(self):
            return model.BrowserItemVector(self._lister(self))

    class Controller(object):
        def __init__(self, browser, library, plugins, index_after):
            self.browser = browser
            self.library = library
            self.plugins = dict((k.upper(), v) for k, v in plugins.items())
            self.index_after = index_after
            self.seen = {}           # path -> listings since first seen
            self.loads = []
            self.listings = 0
            self._items = {}

        # -- the fake indexer ------------------------------------------------------
        def _racks_dir(self):
            return os.path.join(self.library, "LiveBridge", "Racks")

        def _list_racks(self, parent):
            self.listings += 1
            items = []
            folder = self._racks_dir()
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                return items
            for name in names:
                if not name.endswith(".adg"):
                    continue
                path = os.path.join(folder, name)
                count = self.seen.get(path, 0) + 1
                self.seen[path] = count
                if count < self.index_after:
                    continue
                item = self._items.get(path)
                if item is None:
                    item = BrowserItem(name, uri="query:UserLibrary#LiveBridge:Racks:" + name,
                                       is_loadable=True, load_kind="lb_rack")
                    item._lb_path = path
                    self._items[path] = item
                items.append(item)
            return items

        def _list_livebridge(self, parent):
            items = []
            if os.path.isdir(self._racks_dir()):
                items.append(DynamicFolder("Racks", "query:UserLibrary#LiveBridge:Racks",
                                           self._list_racks))
            return items

        # -- loading ---------------------------------------------------------------
        def load(self, item, track):
            info = _parse_rack(item._lb_path)
            self.loads.append(info)
            if len(info["settings"]) > 128:
                raise LiveWouldCrash("more than 128 plug-in parameters in a rack preset")
            for setting in info["settings"]:
                if setting["macro"] >= 0 and setting["range"] is None:
                    raise LiveWouldCrash("macro mapping without a MidiControllerRange slot")
            spec = self.plugins.get(info.get("class_id", ""))
            if spec is None:
                raise RuntimeError("stub: unknown plug-in class id %s" % info.get("class_id"))
            kind = "audio_effect" if info["group"] == "AudioEffectGroupDevice" else "instrument"
            rack = RackDevice(os.path.splitext(os.path.basename(item._lb_path))[0],
                              info["group"], factory._device_type(kind), (), track)
            chain = rack.add_chain(spec["name"])
            plugin = ext.RealPluginDevice(spec["name"], "PluginDevice",
                                          factory._device_type(kind), chain, ("Default",),
                                          spec["names"])
            plugin._lb_state = info.get("processor")
            chain._devices.append(plugin)
            formatters = spec.get("formatters", {})
            params = []
            for slot, setting in enumerate(info["settings"], 1):
                name = spec["ids"].get(setting["id"], "Parameter #%d" % slot)
                param = ext.FormattedParameter(name, spec.get("defaults", {}).get(name, 0.5),
                                               plugin, formatters.get(name))
                plugin._parameters.append(param)
                params.append(param)
            macros = list(rack._parameters)
            for setting, param in zip(info["settings"], params):
                index = setting["macro"]
                if index < 0:
                    continue
                rack._map_macro(index, True)
                macro = macros[index + 1]
                macro._name = info["macro_names"].get(index, macro._name)

                def follow(macro=macro, param=param):
                    param.value = max(0.0, min(1.0, macro.value / 127.0))
                macro.add_value_listener(follow)
            return self.browser._insert(track, rack)

    def setup(browser, library, plugins, index_after=2):
        controller = Controller(browser, library, plugins, index_after)
        root = browser.user_library
        root._children = [c for c in root._children if c.name != "LiveBridge"]
        folder = DynamicFolder("LiveBridge", "query:UserLibrary#LiveBridge",
                               controller._list_livebridge)
        root.add_child(folder)
        original = type(browser).load_item

        def load_item(self, item, /):
            if getattr(item, "_lb_path", None) is None:
                return original(self, item)
            self.loaded_items.append(item)
            if self._hotswap_target is not None:
                raise RuntimeError("stub: plugin_racks must not hot-swap (Live keeps the old "
                                   "plug-in instance)")
            track = self._song._view._selected_track
            return controller.load(item, track)
        browser.load_item = types.MethodType(load_item, browser)
        return controller

    namespace = types.SimpleNamespace(setup=setup, LiveWouldCrash=LiveWouldCrash,
                                      parse_rack=_parse_rack, ext=ext)
    _CACHE["ns"] = namespace
    return namespace


def serum_spec(shipped_map):
    """The plug-in spec for Serum 2 VST3 from the shipped map (names, ids, a few formatters)."""
    from live_stub_ext import plugins_live

    def hz(value):
        return "%.0f Hz" % (8.0 * (2756.25 ** value))

    def ms(value):
        return "%.1f ms" % (value * value * 32000.0)

    return {"name": "Serum 2", "names": plugins_live.serum2_names(),
            "ids": dict((pid, name) for name, pid in shipped_map["parameters"]),
            "formatters": {"Filter 1 Freq": hz, "Env 1 Attack": ms}}


def fake_moduleinfo(folder, name, class_id, vendor="Test Vendor", categories=("Instrument",)):
    """Write ``<folder>/<name>.vst3/Contents/Resources/moduleinfo.json`` (with a comment and
    a trailing comma, as some SDK builds write it); returns the bundle path."""
    bundle = os.path.join(folder, re.sub(r"\s+", "", name) + ".vst3")
    resources = os.path.join(bundle, "Contents", "Resources")
    os.makedirs(resources, exist_ok=True)
    text = ('{\n  // generated by the test\n  "Name": "%s",\n  "Version": "1.0.0",\n'
            '  "Factory Info": {"Vendor": "%s", "URL": "https://example.com/"},\n'
            '  "Classes": [\n    {"CID": "%s", "Category": "Audio Module Class", "Name": "%s",'
            ' "Vendor": "%s", "Version": "1.0.0", "Sub Categories": [%s]},\n'
            '    {"CID": "%s", "Category": "Component Controller Class", "Name": "%s"},\n'
            '  ],\n}\n') % (name, vendor, class_id, name, vendor,
                           ", ".join('"%s"' % c for c in categories), class_id[::-1], name)
    with open(os.path.join(resources, "moduleinfo.json"), "w", encoding="utf-8") as handle:
        handle.write(text)
    return bundle
