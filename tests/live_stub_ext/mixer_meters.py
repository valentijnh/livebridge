"""Stub extension: settable level meters and CPU figures (the shared stub reads 0).

Live 12.4.5 (``docs/LIVE_API_DUMP_12.4.5.md``, Track): ``output_meter_level`` (MIDI or audio,
0..1), ``output_meter_left/right`` (tracks with audio output only — real Live raises
otherwise), ``input_meter_level`` and ``input_meter_left/right`` (audio tracks only),
``performance_impact``; ``Application.average_process_usage`` / ``peak_process_usage`` are
percentages (an idle set read 0.96 / 1.33 on the running Live).

``install(Live)`` returns a namespace with ``set_meters(track, level, left=None, right=None,
input_level=None, input_left=None, input_right=None)``, ``set_cpu(average, peak)`` and
``uninstall()``.  ``left=None`` makes ``output_meter_left/right`` raise like a MIDI-only
track in Live.
"""

import types

_OUTPUT = ("output_meter_level", "output_meter_left", "output_meter_right")
_INPUT = ("input_meter_level", "input_meter_left", "input_meter_right")


def install(Live):
    """Patch the stub's Track meters and Application CPU figures."""
    model = Live._model
    track_cls = model.Track
    app_cls = model.Application
    saved = dict((name, track_cls.__dict__[name]) for name in _OUTPUT + _INPUT)
    saved_app = dict((name, app_cls.__dict__[name]) for name in ("average_process_usage",
                                                                   "peak_process_usage"))
    state = {"cpu": (0.1, 0.2)}

    def meter(name):
        def read(self):
            values = self.__dict__.get("_lb_meters", {})
            value = values.get(name, 0.0)
            if value is None:
                raise RuntimeError("%s: not available for this track" % name)
            return value
        return property(read)

    for name in _OUTPUT + _INPUT:
        setattr(track_cls, name, meter(name))
    app_cls.average_process_usage = property(lambda self: state["cpu"][0])
    app_cls.peak_process_usage = property(lambda self: state["cpu"][1])

    def set_meters(track, level, left=None, right=None, input_level=0.0, input_left=None,
                   input_right=None):
        track.__dict__["_lb_meters"] = {
            "output_meter_level": level, "output_meter_left": left,
            "output_meter_right": right, "input_meter_level": input_level,
            "input_meter_left": input_left, "input_meter_right": input_right}

    def set_cpu(average, peak):
        state["cpu"] = (average, peak)

    def uninstall():
        for name, value in saved.items():
            setattr(track_cls, name, value)
        for name, value in saved_app.items():
            setattr(app_cls, name, value)

    return types.SimpleNamespace(set_meters=set_meters, set_cpu=set_cpu, uninstall=uninstall)
