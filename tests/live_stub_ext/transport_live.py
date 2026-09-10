"""Stub extension for the T1-global tests (transport, scenes, view, cues, record): Live's
*next-tick* deferral, measured on the real Live 12.4.5 Suite on 2026-09-10
(``docs/live_test/T1-global.md``).

Everything else T1 measured is now the shared stub's own behaviour
(``tests/live_stub/Live/_model.py``): song-length limits, ``loop_length`` >= 1, groove
0..1.3125, API arming ignoring Exclusive Arm, scene creation copying the neighbour's
tempo/signature, writable ``Scene.time_signature_enabled``, creation-order cues named "1",
"2" ..., the insert marker snapped to the zoom-dependent Arrangement grid
(``song._arrangement_grid``, driven by ``app.view.zoom_view``), record_mode starting playback
and the double stop.  :func:`set_grid` / :func:`marker` are kept here as test helpers.

With ``install(Live, deferred=True)`` (or :func:`defer` later) the properties Live applies on
its *next main-thread tick* are deferred: ``is_playing`` (``start_playing`` /
``stop_playing`` / ``continue_playing`` / ``play_selection``), ``current_song_time`` (also
``jump_by``, ``CuePoint.jump``, ``jump_to_next_cue`` / ``jump_to_prev_cue``), ``loop``,
``punch_in``, ``punch_out``, ``session_automation_record``, ``record_mode`` (which also starts
playback, like Live's default "Start Playback with Record" preference), ``back_to_arranger``
and a recording fired on an empty armed clip slot.  Reads return the old value until
:func:`tick` runs (call it between two commands, as Live's tick happens between two
requests).  A play request in the same tick as a ``current_song_time`` write starts from the
*old* playhead (``start_playing`` from ``start_time``), exactly as observed.

``install`` returns ``uninstall()`` — always call it (use a fixture).
"""

DEFERRED_FLAGS = ("loop", "punch_in", "punch_out", "session_automation_record",
                  "record_mode", "back_to_arranger")

_STATE = {"deferred": False, "Live": None, "orig": {}}


def _song(song=None):
    if song is not None:
        return song
    Live = _STATE["Live"]
    app = Live.Application.get_application() if Live is not None else None
    return app.get_document() if app is not None else None


def set_grid(beats, song=None):
    """Set the Arrangement grid (what Live derives from the zoom level) of ``song``
    (default: the song of the installed application)."""
    _song(song)._arrangement_grid = float(beats)


def grid(song=None):
    """The current Arrangement grid in beats."""
    return _song(song)._arrangement_grid


def marker(song):
    """Where Live's insert marker is (stopped)."""
    return song._marker()


def _pending(song):
    return song.__dict__.setdefault("_ext_pending", {})


def defer(enabled=True):
    """Switch the next-tick deferral on/off after :func:`install` (tests use a fixture)."""
    _STATE["deferred"] = bool(enabled)


def tick(song):
    """Apply everything Live would apply on its next tick."""
    pending = _pending(song)
    if not pending:
        return
    Song = type(song)
    ops = dict(pending)
    pending.clear()
    play = ops.pop("play", None)
    if play is not None:
        ops.pop("position", None)      # lost when a play starts in the same tick
        Song._apply_transport(song, play)
    elif "position" in ops:
        reported, mark, start = ops.pop("position")
        Song._apply_move(song, reported, mark, start)
    for name in DEFERRED_FLAGS:
        if name in ops:
            setattr(song, "_" + name, ops.pop(name))
            if name == "record_mode" and song._record_mode and not song._is_playing:
                Song._apply_transport(song, "start")
    for slot in ops.pop("record_slots", ()):
        slot.__dict__.pop("_ext_record_pending", None)
        _start_recording(song, slot)


def _start_recording(song, slot):
    Live = _STATE["Live"]
    track = slot._track()
    if track is None or slot._clip is not None:
        return
    index = track._clip_slots.index(slot)
    length = slot.__dict__.pop("_ext_record_length", None)
    clip = Live.Clip.Clip("%s %d" % (track._name, index + 1), float(length or 4.0),
                          track.has_midi_input, slot)
    clip._is_recording = True
    clip._is_playing = True
    slot._set_clip(clip)
    track._playing_slot_index = index
    song._session_record = True
    song._session_record_status = 1
    if not song._is_playing:
        song._is_playing = True


def install(Live, deferred=False):
    """Patch the stub classes for the switchable deferral; returns ``uninstall()``."""
    Song = Live.Song.Song
    ClipSlot = Live.ClipSlot.ClipSlot
    _STATE["deferred"] = bool(deferred)
    _STATE["Live"] = Live
    orig = _STATE["orig"] = {"_move": Song.__dict__["_move"],
                             "_transport": Song.__dict__["_transport"]}
    saved = []

    def patch(cls, name, value):
        saved.append((cls, name, cls.__dict__.get(name, _MISSING)))
        setattr(cls, name, value)

    def move(self, reported, marker=None, start=None):
        if _STATE["deferred"]:
            _pending(self)["position"] = (reported, marker, start)
        else:
            orig["_move"](self, reported, marker, start)

    def transport(self, kind):
        if _STATE["deferred"]:
            _pending(self)["play"] = kind
        else:
            orig["_transport"](self, kind)

    def jump_by(self, beats, /):
        pending = _pending(self).get("position")
        base = pending[0] if pending else self._current_song_time
        # only the reported time moves; Live's insert marker stays (measured)
        self._move(max(0.0, base + float(beats)), self._marker())

    patch(Song, "_move", move)
    patch(Song, "_transport", transport)
    patch(Song, "jump_by", jump_by)
    patch(Song, "scrub_by", jump_by)

    def flag(name, base):
        def fset(self, value):
            if _STATE["deferred"]:
                _pending(self)[name] = bool(value)
            else:
                base.fset(self, value)
        return property(base.fget, fset)

    for name in DEFERRED_FLAGS:
        patch(Song, name, flag(name, Song.__dict__[name]))

    # -- recording into an empty armed slot starts on the next tick ------------------------
    original_slot_fire = ClipSlot.__dict__["fire"]

    def slot_fire(self, record_length=None, launch_quantization=None, force_legato=False):
        track = self._track()
        if _STATE["deferred"] and self._clip is None and track is not None and \
                getattr(track, "_arm", False):
            self.__dict__["_ext_record_length"] = record_length
            self.__dict__["_ext_record_pending"] = True
            song = track._canonical_parent
            _pending(song).setdefault("record_slots", []).append(self)
            return
        original_slot_fire(self, record_length, launch_quantization, force_legato)

    patch(ClipSlot, "fire", slot_fire)

    def uninstall():
        for cls, name, value in reversed(saved):
            if value is _MISSING:
                delattr(cls, name)
            else:
                setattr(cls, name, value)
        _STATE["deferred"] = False

    return uninstall


class _Missing(object):
    pass


_MISSING = _Missing()
