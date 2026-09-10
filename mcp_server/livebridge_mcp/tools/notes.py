"""MIDI note tools: read notes compactly, add / replace / remove / modify them, transform them
(quantize, humanize, transpose, velocity, legato) and compose with step patterns and chord
symbols.

Pitch convention (Ableton's): **C3 = 60** (middle C), so C1 = 36 = the first Drum Rack pad,
C-2 = 0, G8 = 127. Pitches may be numbers 0..127, names ("C3", "F#4", "Db2") or General-MIDI
drum names ("kick" 36, "snare" 38, "clap" 39, "hat" 42, "ohh" 46, "crash" 49, "ride" 51 ...).
Times and durations are beats (quarter notes): in 4/4 a bar is 4 beats, a 16th note 0.25.

Clip addressing is the same as for the `live_clip_*` tools: `track` + `slot`, or `clip`
(LOM path, clip name or "selected"). Arrangement clips work via their path.

Keys: `key="song"` (default) is the song's root + scale (Live 12 Scale), or "A minor",
"F# dorian", "Bb", "Am", {"root": "A", "scale": "minor"}. Chords can be symbols ("Am7"), Roman
numerals in the key ("I V vi IV", "ii7 V7 Imaj7", "bVII") or scale degrees ("1 5 6 4").
"""

from __future__ import annotations

import re
from typing import Any

from . import bridge_call, drop_none, tool_error
from ..client import BridgeClient
from .clips import address_args, clip_address

Ref = int | str
Pitch = int | str

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])([#b♯♭]*)(-?\d+)$")

#: General-MIDI drum names -> note (mirrors the Remote Script's table).
DRUM_NOTES = {
    "kick": 36, "bd": 36, "bassdrum": 36, "kick2": 35, "rim": 37, "rimshot": 37,
    "sidestick": 37, "snare": 38, "sd": 38, "clap": 39, "handclap": 39, "snare2": 40,
    "floortom": 41, "lowtom": 45, "tomlow": 45, "midtom": 47, "tommid": 47,
    "hightom": 50, "tomhigh": 50, "hat": 42, "hh": 42, "hihat": 42, "chh": 42,
    "closedhat": 42, "closedhihat": 42, "pedalhat": 44, "ohh": 46, "openhat": 46,
    "openhihat": 46, "crash": 49, "ride": 51, "china": 52, "ridebell": 53,
    "tambourine": 54, "splash": 55, "cowbell": 56, "crash2": 57, "shaker": 70,
    "maracas": 70, "clave": 75, "woodblock": 76,
}

_STEP_CHARS = set("xXoO_=.-0123456789")
_NOTE_FIELDS = {"pitch", "start", "start_time", "time", "duration", "length", "velocity",
                "mute", "probability", "velocity_deviation", "release_velocity"}
_CHANGE_FIELDS = {"note_id", "pitch", "start", "start_time", "duration", "velocity", "mute",
                  "probability", "velocity_deviation", "release_velocity"}
_VOICINGS = ("close", "open", "drop2", "drop3", "spread")
_ARP_STYLES = ("up", "down", "updown", "downup", "random", "chord", "root", "root_fifth",
               "octave", "pedal")


def note_to_midi(value: Any) -> int:
    """MIDI number from 0..127, "60", a name ("C3" = 60, "F#4", "Db2") or a GM drum name.

    Raises:
        ValueError: with a friendly message when the value is not a note.
    """
    if isinstance(value, bool):
        raise ValueError(f"{value!r} is not a note")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if 0 <= value <= 127:
            return value
        raise ValueError(f"pitch {value} is outside the MIDI range 0..127")
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return note_to_midi(int(text))
        drum = DRUM_NOTES.get(re.sub(r"[\s_\-]+", "", text.lower()))
        if drum is not None:
            return drum
        match = _NOTE_RE.match(text)
        if match:
            letter, accidentals, octave = match.groups()
            pitch = (int(octave) + 2) * 12 + _PITCH_CLASS[letter.upper()]
            pitch += accidentals.count("#") + accidentals.count("♯")
            pitch -= accidentals.count("b") + accidentals.count("♭")
            if 0 <= pitch <= 127:
                return pitch
            raise ValueError(f"{value!r} is outside the MIDI range (C-2..G8)")
    raise ValueError(f"{value!r} is not a note — use 0..127, a name like C3 (=60), F#4, Db2, "
                     "or a drum name like kick/snare/hat")


def midi_to_note(pitch: int) -> str:
    """60 -> "C3" (Ableton's convention, sharps)."""
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 2}"


def _pitch_arg(value: Any, name: str, cmd: str) -> tuple[Any, dict[str, Any] | None]:
    """Validate a pitch filter (one pitch or a list); returns (normalised, error)."""
    if value is None:
        return None, None
    try:
        if isinstance(value, list):
            return [note_to_midi(v) for v in value], None
        return note_to_midi(value), None
    except ValueError as exc:
        return None, tool_error(f"{name}: {exc}", cmd=cmd)


def _normalise_notes(notes: Any, cmd: str) -> tuple[list[Any], dict[str, Any] | None]:
    """Check a notes list and convert pitch names to numbers."""
    if not isinstance(notes, list):
        return [], tool_error("notes must be a list of {pitch, start, duration, velocity?} "
                              "objects or [pitch, start, duration, velocity?] lists", cmd=cmd)
    result: list[Any] = []
    for index, note in enumerate(notes):
        where = f"notes[{index}]"
        if isinstance(note, dict):
            unknown = set(note) - _NOTE_FIELDS
            if unknown:
                return [], tool_error(f"{where}: unknown field(s) {', '.join(sorted(unknown))}",
                                      cmd=cmd)
            if "pitch" not in note:
                return [], tool_error(f"{where} needs a pitch", cmd=cmd)
            if not any(k in note for k in ("start", "start_time", "time")):
                return [], tool_error(f"{where} needs a start (beats)", cmd=cmd)
            if not any(k in note for k in ("duration", "length")):
                return [], tool_error(f"{where} needs a duration (beats)", cmd=cmd)
            item = dict(note)
            try:
                item["pitch"] = note_to_midi(note["pitch"])
            except ValueError as exc:
                return [], tool_error(f"{where}: {exc}", cmd=cmd)
            duration = item.get("duration", item.get("length"))
            if isinstance(duration, (int, float)) and duration <= 0:
                return [], tool_error(f"{where}: duration must be > 0", cmd=cmd)
            velocity = item.get("velocity")
            if isinstance(velocity, (int, float)) and not 0 <= velocity <= 127:
                return [], tool_error(f"{where}: velocity must be 1..127", cmd=cmd)
            result.append(item)
        elif isinstance(note, list):
            if not 3 <= len(note) <= 6:
                return [], tool_error(f"{where}: a list note is [pitch, start, duration, "
                                      "velocity?, mute?, probability?]", cmd=cmd)
            item_list = list(note)
            try:
                item_list[0] = note_to_midi(note[0])
            except ValueError as exc:
                return [], tool_error(f"{where}: {exc}", cmd=cmd)
            if isinstance(note[2], (int, float)) and note[2] <= 0:
                return [], tool_error(f"{where}: duration must be > 0", cmd=cmd)
            result.append(item_list)
        else:
            return [], tool_error(f"{where} must be an object or a list", cmd=cmd)
    return result, None


_NOTE_LIKE_RE = re.compile(r"^[A-Za-z][#b♯♭]?-?\d+$")


def _check_pattern(pattern: dict[str, str], cmd: str) -> dict[str, Any] | None:
    if not pattern:
        return tool_error('pattern must map rows to step strings, e.g. {"kick": "x...x..."}',
                          cmd=cmd)
    for key, steps in pattern.items():
        try:
            note_to_midi(key)
        except ValueError as exc:
            # Other names may be Drum Rack pad names ("Perc 2") — resolved in Live.
            if not key.strip() or key.strip().lstrip("-").isdigit() or \
                    _NOTE_LIKE_RE.match(key.strip()):
                return tool_error(f"pattern key {key!r}: {exc}", cmd=cmd)
        cleaned = re.sub(r"[\s|]+", "", steps)
        bad = sorted(set(cleaned) - _STEP_CHARS)
        if bad:
            return tool_error(
                f"pattern[{key!r}] has unknown step character(s) {' '.join(bad)} — use x (hit), "
                "X (accent), o (ghost), 1-9 (velocity level), _ (hold), . or - (rest)", cmd=cmd)
        if not cleaned:
            return tool_error(f"pattern[{key!r}] has no steps", cmd=cmd)
    return None


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the MIDI note tools on the MCP app."""

    @mcp.tool()
    def live_clip_get_notes(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float | None = None,
        end: float | None = None,
        pitch: Pitch | list[Pitch] | None = None,
        pitch_min: Pitch | None = None,
        pitch_max: Pitch | None = None,
        offset: int = 0,
        limit: int = 512,
        include_expression: bool = False,
        pitch_names: bool = False,
        selected: bool = False,
    ) -> Any:
        """Read the notes of a MIDI clip in a compact table.

        Args:
            track, slot / clip: Which clip.
            start, end: Only notes starting in [start, end) beats (clip time).
            pitch: One pitch or a list ("C1", 36, "kick", ["C1", "D1"]).
            pitch_min, pitch_max: Pitch range (inclusive), when `pitch` is not given.
            offset, limit: Paging (limit 1..2000); notes are sorted by start then pitch.
            include_expression: Append velocity_deviation and release_velocity to each row.
            pitch_names: Return pitch names instead of numbers — Live's own spelling for the
                clip's key (e.g. "B♭2"), else "C3"-style sharps.
            selected: Only the notes the user selected in Live's clip editor.

        Returns:
            {"clip": path, "name", "length", "loop": [start, end], "total", "count",
            "fields": ["pitch","start","duration","velocity","mute","probability","note_id"],
            "notes": [[36, 0, 0.5, 100, false, 1, 17], ...], "next_offset"?}

        Gotchas: `note_id` is what `live_clip_modify_notes`, `live_clip_remove_notes` and
        `live_clip_transform_notes` take. Notes outside the loop are included.
        """
        cmd = "notes.get"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if offset < 0 or not 1 <= limit <= 2000:
            return tool_error("offset must be >= 0 and limit 1..2000", cmd=cmd)
        if start is not None and end is not None and end <= start:
            return tool_error("end must be after start", cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        low, error = _pitch_arg(pitch_min, "pitch_min", cmd)
        if error:
            return error
        high, error = _pitch_arg(pitch_max, "pitch_max", cmd)
        if error:
            return error
        args = address_args(track, slot, clip)
        args.update(drop_none(start=start, end=end, pitch=pitch_value, pitch_min=low,
                              pitch_max=high, offset=offset, limit=limit))
        if include_expression:
            args["include_expression"] = True
        if pitch_names:
            args["pitch_names"] = True
        if selected:
            args["selected"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_add_notes(
        notes: list[Any],
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        replace: bool = False,
        replace_start: float | None = None,
        replace_end: float | None = None,
        create: bool = True,
        extend: bool = True,
    ) -> Any:
        """Add MIDI notes to a clip — optionally replacing what is there. Creates the clip when
        the slot is empty and grows its loop to fit the notes (both on by default).

        Args:
            notes: List of {"pitch": 60 or "C3", "start": beats, "duration": beats,
                "velocity": 1..127 (default 100), "mute": false, "probability": 0..1,
                "velocity_deviation": -127..127, "release_velocity": 0..127}, or compact lists
                [pitch, start, duration, velocity?, mute?, probability?].
                Example: [{"pitch": "C3", "start": 0, "duration": 1}, ["E3", 1, 1, 90]].
            track, slot / clip: Which clip.
            replace: First remove existing notes — the whole clip, or only the notes starting in
                [replace_start, replace_end) when given. `notes=[]` with replace just clears.
            replace_start, replace_end: The region to clear (beats) when `replace` is true.
            create: If the slot is empty, create a MIDI clip long enough for the notes (whole
                bars) — default true. With only `track` (no `slot`), the track's first empty
                slot is used. false = fail on an empty slot.
            extend: Grow the clip's loop (whole bars) when notes end after it (default true;
                false keeps the loop, notes past it are stored but not heard).

        Returns:
            {"clip": path, "name", "length", "added": n, "note_ids": [...], "removed"?: n,
            "created"?: true, "extended_to"?: beats, "merged"?: n}

        Gotchas: start times are clip-relative beats, not song time. Live never keeps two overlapping notes of
        the same pitch: a new note replaces one with the same pitch+start, swallows same-pitch
        notes starting inside it and shortens one it starts inside of — `merged` counts the
        notes that disappeared (`added`/`note_ids` are what Live kept).
        """
        replace_mode = bool(replace or replace_start is not None or replace_end is not None)
        cmd = "notes.replace" if replace_mode else "notes.add"
        if clip is None and track is not None and slot is None and not create:
            return tool_error("slot is required (or pass create=true to use the first empty "
                              "slot)", cmd=cmd)
        if clip is None and track is None:
            return clip_address(track, slot, clip, cmd)
        normalised, error = _normalise_notes(notes, cmd)
        if error:
            return error
        if not normalised and not replace_mode:
            return tool_error("notes must not be empty", cmd=cmd)
        if replace_start is not None and replace_end is not None and replace_end <= replace_start:
            return tool_error("replace_end must be after replace_start", cmd=cmd)
        args = address_args(track, slot, clip)
        args["notes"] = normalised
        if replace_mode:
            args.update(drop_none(start=replace_start, end=replace_end))
        args["create"] = bool(create)
        args["extend"] = bool(extend)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_remove_notes(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float | None = None,
        end: float | None = None,
        pitch: Pitch | list[Pitch] | None = None,
        pitch_min: Pitch | None = None,
        pitch_max: Pitch | None = None,
        note_ids: list[int] | None = None,
        selected: bool = False,
    ) -> Any:
        """Remove notes from a MIDI clip: all of them, a time/pitch region, or specific ids.

        Args:
            track, slot / clip: Which clip.
            start, end: Remove notes starting in [start, end) beats.
            pitch: One pitch or a list ("kick", "C1", 36); or pitch_min / pitch_max range.
            note_ids: Only these notes (ids from `live_clip_get_notes`).
            selected: Only the notes selected in Live's clip editor.

        Returns:
            {"clip", "name", "length", "removed": n}

        Gotchas: with no filters every note in the clip is removed (one undo step in Live).
        """
        cmd = "notes.clear"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if start is not None and end is not None and end <= start:
            return tool_error("end must be after start", cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        low, error = _pitch_arg(pitch_min, "pitch_min", cmd)
        if error:
            return error
        high, error = _pitch_arg(pitch_max, "pitch_max", cmd)
        if error:
            return error
        args = address_args(track, slot, clip)
        args.update(drop_none(start=start, end=end, pitch=pitch_value, pitch_min=low,
                              pitch_max=high, note_ids=note_ids))
        if selected:
            args["selected"] = True
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_modify_notes(
        changes: list[dict[str, Any]],
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
    ) -> Any:
        """Edit existing notes by id, keeping their ids and per-note expression.

        Args:
            changes: List of {"note_id": id, plus any of "pitch" (number or name), "start",
                "duration", "velocity", "mute", "probability", "velocity_deviation",
                "release_velocity"}. Example: [{"note_id": 17, "velocity": 64},
                {"note_id": 18, "pitch": "G3", "duration": 0.5}].
            track, slot / clip: Which clip.

        Returns:
            {"clip", "name", "length", "modified": n, "merged"?: n}

        Gotchas: get ids with `live_clip_get_notes`. An unknown id fails the whole call. For bulk
        edits (transpose everything, scale velocities) `live_clip_transform_notes` is simpler.
        Live resolves same-pitch overlaps afterwards: moving a note onto another note's start
        (same pitch) deletes one of them (`merged`), a note lengthened into the next one is cut
        at its start.
        """
        cmd = "notes.modify"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if not changes:
            return tool_error("changes must be a non-empty list of {note_id, ...}", cmd=cmd)
        normalised = []
        for index, change in enumerate(changes):
            if "note_id" not in change:
                return tool_error(f"changes[{index}] needs a note_id", cmd=cmd)
            unknown = set(change) - _CHANGE_FIELDS
            if unknown:
                return tool_error(f"changes[{index}]: unknown field(s) "
                                  f"{', '.join(sorted(unknown))}", cmd=cmd)
            if len(change) == 1:
                return tool_error(f"changes[{index}] changes nothing", cmd=cmd)
            item = dict(change)
            if "pitch" in item:
                try:
                    item["pitch"] = note_to_midi(item["pitch"])
                except ValueError as exc:
                    return tool_error(f"changes[{index}]: {exc}", cmd=cmd)
            normalised.append(item)
        args = address_args(track, slot, clip)
        args["changes"] = normalised
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_transform_notes(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float | None = None,
        end: float | None = None,
        pitch: Pitch | list[Pitch] | None = None,
        pitch_min: Pitch | None = None,
        pitch_max: Pitch | None = None,
        note_ids: list[int] | None = None,
        quantize: float | str | None = None,
        strength: float = 1.0,
        swing: float = 0.0,
        quantize_ends: bool = False,
        humanize_timing: float = 0.0,
        humanize_velocity: float = 0.0,
        seed: int | None = None,
        transpose: int = 0,
        velocity_scale: float | None = None,
        velocity_offset: float = 0.0,
        velocity_set: float | None = None,
        velocity_min: float = 1,
        velocity_max: float = 127,
        legato: bool = False,
        fix_overlaps: bool = False,
        gap: float = 0.0,
        shift: float = 0.0,
        retrograde: bool = False,
        transpose_steps: int = 0,
        invert: bool | Pitch | None = None,
        fit_scale: bool | str | None = None,
        key: str | dict[str, Any] | None = None,
        selected: bool = False,
    ) -> Any:
        """Bulk-edit notes: move in time, quantize, humanize, reverse, transpose (semitones or
        scale steps in key), invert, fit to a scale, change velocities, legato / fix overlaps —
        any combination in one call (one undo step). Order: shift → quantize → humanize →
        retrograde → transpose → transpose_steps → invert → fit_scale → velocity →
        legato/fix_overlaps.

        Args:
            track, slot / clip: Which clip.
            Selection (default: every note): start/end (note starts in [start, end) beats),
                pitch (one or a list, names ok) or pitch_min/pitch_max, note_ids, selected
                (the notes the user selected in Live's clip editor).
            shift: Move the notes by this many beats (1 = one beat later, -0.5 earlier).
            quantize: Grid in beats (0.25) or a note value: "1/4", "1/8", "1/16", "1/32",
                with "T" for triplets ("1/8T") or "." for dotted ("1/8.").
            strength: Quantize strength 0..1 (0.5 = halfway to the grid).
            swing: 0..1 — delay every second grid step by that fraction of a step (~0.33 = shuffle).
            quantize_ends: Also snap note ends to the grid.
            humanize_timing: Max random timing shift in beats (0.01–0.03 is subtle).
            humanize_velocity: Max random velocity change (5–15 is natural).
            seed: Integer for reproducible humanizing; the seed used is returned.
            retrograde: Play the selection backwards (mirrored in time).
            transpose: Semitones (+12 = one octave up, -7 = a fifth down).
            transpose_steps: Scale degrees in `key` (+2 = up a diatonic third, stays in key).
            invert: Mirror pitches around the first note (true) or around a pitch ("C3").
            fit_scale: Snap every pitch into `key`: true/"nearest", "up" or "down".
            key: Scale for transpose_steps/fit_scale: "song" (default: the song's key),
                "A minor", "F# dorian", {"root": "A", "scale": "minor"}.
            velocity_scale: Multiply velocities (0.8 = 20% softer).
            velocity_offset: Add to velocities (-10).
            velocity_set: Set every selected velocity to this value.
            velocity_min, velocity_max: Clamp velocities to this range afterwards
                (compress dynamics, e.g. 80..110).
            legato: Stretch each note to the start of the next one (last note to the clip end).
            fix_overlaps: Shorten notes that overlap the next note of the same pitch.
            gap: Beats left free by legato / fix_overlaps (0.01 = tiny gap).

        Returns:
            {"clip", "name", "length", "selected": n, "operations": [...], "seed"?, "key"?,
            "axis"?, "fitted"?, "merged"?}

        Gotchas: a transpose/shift that would push notes outside 0..127 / before beat 0 fails
        and changes nothing. To copy notes elsewhere use `live_clip_duplicate_notes`.
        When quantize/humanize lands two same-pitch notes on one start Live keeps only one
        (`merged` counts the deleted ones).
        `live_clip_edit(action="quantize")` is Live's own quantize (fixed grids, whole clip).
        """
        cmd = "notes.transform"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        checks = ((strength, "strength", 0.0, 1.0), (swing, "swing", 0.0, 1.0),
                  (humanize_timing, "humanize_timing", 0.0, 4.0),
                  (humanize_velocity, "humanize_velocity", 0.0, 127.0),
                  (transpose, "transpose", -127, 127),
                  (velocity_min, "velocity_min", 1, 127), (velocity_max, "velocity_max", 1, 127),
                  (gap, "gap", 0.0, 16.0))
        for value, label, low, high in checks:
            if not low <= value <= high:
                return tool_error(f"{label} must be between {low} and {high}", cmd=cmd)
        if velocity_min > velocity_max:
            return tool_error("velocity_min must not exceed velocity_max", cmd=cmd)
        if velocity_set is not None and not 1 <= velocity_set <= 127:
            return tool_error("velocity_set must be 1..127", cmd=cmd)
        if velocity_scale is not None and velocity_scale < 0:
            return tool_error("velocity_scale must be >= 0", cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        low_pitch, error = _pitch_arg(pitch_min, "pitch_min", cmd)
        if error:
            return error
        high_pitch, error = _pitch_arg(pitch_max, "pitch_max", cmd)
        if error:
            return error
        operations = drop_none(quantize=quantize, velocity_scale=velocity_scale,
                               velocity_set=velocity_set)
        if humanize_timing:
            operations["humanize_timing"] = humanize_timing
        if humanize_velocity:
            operations["humanize_velocity"] = humanize_velocity
        if transpose:
            operations["transpose"] = transpose
        if velocity_offset:
            operations["velocity_offset"] = velocity_offset
        if velocity_min != 1:
            operations["velocity_min"] = velocity_min
        if velocity_max != 127:
            operations["velocity_max"] = velocity_max
        if legato:
            operations["legato"] = True
        if fix_overlaps:
            operations["fix_overlaps"] = True
        if shift:
            operations["shift"] = shift
        if retrograde:
            operations["retrograde"] = True
        if transpose_steps:
            if not -70 <= transpose_steps <= 70:
                return tool_error("transpose_steps must be -70..70", cmd=cmd)
            operations["transpose_steps"] = transpose_steps
        if invert is not None and invert is not False:
            if not isinstance(invert, bool):
                try:
                    invert = note_to_midi(invert)
                except ValueError as exc:
                    return tool_error(f"invert: {exc}", cmd=cmd)
            operations["invert"] = invert
        if fit_scale is not None and fit_scale is not False:
            if fit_scale is not True and fit_scale not in ("nearest", "up", "down"):
                return tool_error('fit_scale must be true, "nearest", "up" or "down"', cmd=cmd)
            operations["fit_scale"] = fit_scale
        if not operations:
            return tool_error("nothing to do — pass shift, quantize, humanize_*, retrograde, "
                              "transpose, transpose_steps, invert, fit_scale, velocity_*, "
                              "legato or fix_overlaps", cmd=cmd)
        if key is not None and (transpose_steps or operations.get("fit_scale") is not None):
            operations["key"] = key
        if quantize is not None:
            if strength != 1.0:
                operations["strength"] = strength
            if swing:
                operations["swing"] = swing
            if quantize_ends:
                operations["quantize_ends"] = True
        if (legato or fix_overlaps) and gap:
            operations["gap"] = gap
        if seed is not None and (humanize_timing or humanize_velocity):
            operations["seed"] = seed
        args = address_args(track, slot, clip)
        args.update(drop_none(start=start, end=end, pitch=pitch_value, pitch_min=low_pitch,
                              pitch_max=high_pitch, note_ids=note_ids))
        if selected:
            args["selected"] = True
        args.update(operations)
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_write_pattern(
        pattern: dict[str, str],
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        step: float | str = 0.25,
        start: float = 0.0,
        velocity: float = 100,
        accent_velocity: float = 127,
        ghost_velocity: float = 50,
        gate: float = 0.9,
        repeat: int = 1,
        swing: float = 0.0,
        clear: bool = True,
        create: bool = True,
        extend: bool = True,
        kit: bool = True,
    ) -> Any:
        """Write a step-sequencer pattern (drums, arps, basslines) — one string per pitch.

        Args:
            pattern: {row: steps}. Row = drum name ("kick", "snare", "clap", "hat", "ohh", "ride",
                "crash", "rim", "tom_low", "tom_mid", "tom_high", "cowbell", "shaker"), a note
                name ("C1" = 36 = first Drum Rack pad, "F#1" = 42) or a number. Steps, one
                character per step: `x` hit, `X` accent, `o` ghost note, `1`-`9` velocity level
                (9 = 127), `_` hold the previous hit one more step, `.` or `-` rest. Spaces and
                `|` are ignored, so "x...|x...|x...|x..." is fine.
            track, slot / clip: Target clip. With `create` (default) an empty slot gets a new
                clip sized to the pattern; with only `track`, the first empty slot is used.
            step: Step length in beats or as a note value — 0.25 / "1/16" (default), "1/8",
                "1/8T", "1/32".
            start: Beat where the pattern starts in the clip.
            velocity, accent_velocity, ghost_velocity: Velocities for x / X / o.
            gate: Note length as a fraction of a step (0.05..1).
            repeat: Write the pattern this many times back to back.
            swing: 0..1 fraction of a step to delay every second step (0.2–0.35 = groove).
            clear: First remove existing notes of those rows in the written range (default).
            create: Create the MIDI clip when the slot is empty (default true).
            extend: Grow the clip loop when the pattern is longer (default true).
            kit: Map drum names onto the track's Drum Rack by pad name (default true): "hat"
                goes to the pad called e.g. "HH Closed 808" even if it is not on F#1, "clap"
                to the clap pad, and a row may be named after a pad ("Perc 2"). Note names and
                numbers are always used as given. false = fixed General-MIDI notes.

        Returns:
            {"clip": path, "name", "length", "added": n, "removed": n, "rows": {row: pitch},
            "pattern_length": beats, "kit"?: {"device", "rows": {row: {"note", "pad", "how"}}},
            "warnings"?: [rows that hit an empty pad], "created"?, "extended_to"?, "merged"?}

        Example: pattern={"kick": "x...x...x...x...", "snare": "....X.......X...",
        "hat": "x.x.x.x.x.x.x.xo"} → a 1-bar four-on-the-floor beat in 16ths.
        """
        cmd = "notes.write_pattern"
        if clip is None and track is None:
            return clip_address(track, slot, clip, cmd)
        error = _check_pattern(pattern, cmd)
        if error:
            return error
        for value, label, low, high in ((velocity, "velocity", 1, 127),
                                        (accent_velocity, "accent_velocity", 1, 127),
                                        (ghost_velocity, "ghost_velocity", 1, 127),
                                        (gate, "gate", 0.05, 1.0), (repeat, "repeat", 1, 256),
                                        (swing, "swing", 0.0, 1.0)):
            if not low <= value <= high:
                return tool_error(f"{label} must be between {low} and {high}", cmd=cmd)
        if start < 0:
            return tool_error("start must be >= 0", cmd=cmd)
        if isinstance(step, (int, float)) and step <= 0:
            return tool_error("step must be > 0 beats", cmd=cmd)
        args = address_args(track, slot, clip)
        args.update(pattern=pattern, step=step, start=start, velocity=velocity,
                    accent_velocity=accent_velocity, ghost_velocity=ghost_velocity, gate=gate,
                    repeat=repeat, swing=swing, clear=clear, create=create, extend=extend)
        if not kit:
            args["kit"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_write_chords(
        chords: list[Any] | str,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float = 0.0,
        duration: float | None = None,
        octave: int = 3,
        velocity: float = 90,
        voicing: str = "close",
        inversion: int = 0,
        voice_leading: bool = False,
        bass: bool = False,
        strum: float = 0.0,
        clear: bool = True,
        create: bool = True,
        extend: bool = True,
        key: str | dict[str, Any] | None = None,
    ) -> Any:
        """Write a chord progression from chord symbols, Roman numerals or scale degrees.

        Args:
            chords: ["Am7", "F", "C", "G/B"] (each lasts `duration`), a string "Am7 F C G", or
                objects {"chord": "Dm9", "start": beats?, "duration": beats?, "velocity"?,
                "octave"?, "inversion"?} for rhythm. "N.C." = a rest. Chords without `start`
                follow each other.
                Understood: C Cm Cdim Caug Csus2 Csus4 C5 C6 Cm6 C6/9 C7 Cmaj7 Cm7 Cm7b5 (Cø)
                Cdim7 CmMaj7 Caug7 C7sus4 C9 Cmaj9 Cm9 Cadd9 C11 Cm11 C13, alterations b5 #5 b9
                #9 #11 b13, no3/no5, slash chords (G/B puts B in the bass). Flats: Bb, Eb.
                Roman numerals in `key` (case = major/minor): "I V vi IV", "ii7 V7 Imaj7",
                "i bVI bIII bVII", "vii°", "iiø7"; scale degrees: "1 5 6 4", "27 57 17"
                (diatonic sevenths).
            track, slot / clip: Target clip (created when missing, like write_pattern).
            start: Beat of the first chord.
            duration: Default chord length in beats (default one bar).
            octave: Octave of the chord root, C3 = 60 (default 3).
            velocity: 1..127.
            voicing: "close" (default), "open", "drop2", "drop3" or "spread".
            inversion: 0 root position, 1 first inversion, 2 second ...
            voice_leading: Choose inversions that move the least between chords (smooth pads).
            bass: Also play the root (or the slash note) an octave below.
            strum: Delay between successive notes of a chord in beats (0.02–0.05 = strum).
            clear: First remove the notes in the written time range (default).
            create, extend: Create the clip when missing / grow its loop (default true).
            key: Key for Roman numerals / degrees: "song" (default: the song's root + scale),
                "A minor", "F# dorian", "Bb", {"root": "A", "scale": "minor"}.

        Returns:
            {"clip", "name", "length", "added": n, "removed": n,
            "chords": [{"chord", "start", "duration", "pitches": [...], "notes": ["A3", ...]}],
            "key"?, "created"?, "extended_to"?, "merged"?}
        """
        cmd = "notes.write_chords"
        if clip is None and track is None:
            return clip_address(track, slot, clip, cmd)
        if isinstance(chords, str):
            chords = [c for c in re.split(r"[\s|,]+", chords.strip()) if c]
        if not chords:
            return tool_error('chords must not be empty, e.g. ["Am7", "F", "C", "G"]', cmd=cmd)
        for index, chord in enumerate(chords):
            symbol = chord.get("chord") if isinstance(chord, dict) else chord
            if not isinstance(symbol, str) or not symbol.strip():
                return tool_error(f"chords[{index}] must be a chord symbol or "
                                  '{"chord": ..., "start"?, "duration"?}', cmd=cmd)
        if voicing not in _VOICINGS:
            return tool_error(f"voicing must be one of {', '.join(_VOICINGS)}", cmd=cmd)
        for value, label, low, high in ((octave, "octave", -1, 8),
                                        (velocity, "velocity", 1, 127),
                                        (inversion, "inversion", 0, 12),
                                        (strum, "strum", 0.0, 4.0)):
            if not low <= value <= high:
                return tool_error(f"{label} must be between {low} and {high}", cmd=cmd)
        if duration is not None and duration <= 0:
            return tool_error("duration must be > 0 beats", cmd=cmd)
        if start < 0:
            return tool_error("start must be >= 0", cmd=cmd)
        args = address_args(track, slot, clip)
        args.update(drop_none(chords=chords, start=start, duration=duration, octave=octave,
                              velocity=velocity, voicing=voicing, inversion=inversion,
                              voice_leading=voice_leading, bass=bass, strum=strum, clear=clear,
                              create=create, extend=extend, key=key))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_write_arp(
        chords: list[Any] | str,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        key: str | dict[str, Any] | None = None,
        style: str = "up",
        rate: float | str = "1/16",
        start: float = 0.0,
        duration: float | None = None,
        octaves: int = 1,
        octave: int = 3,
        gate: float = 0.8,
        velocity: float = 100,
        accent_velocity: float | None = None,
        swing: float = 0.0,
        seed: int | None = None,
        clear: bool = True,
        create: bool = True,
        extend: bool = True,
    ) -> Any:
        """Write an arpeggio — or a simple bassline — from a chord progression in one call.

        Args:
            chords: Like live_clip_write_chords: "Am7 F C G", Roman numerals in `key`
                ("i VI III VII"), degrees ("1 6 4 5") or objects {"chord", "start"?,
                "duration"?, "octave"?}.
            track, slot / clip: Target clip (created when missing).
            key: "song" (default), "A minor", ... — for numerals/degrees.
            style: "up", "down", "updown", "downup", "random" (seeded), "chord" (repeated
                stabs), "root" (bassline: root on every step), "root_fifth", "octave"
                (root / octave up), "pedal" (root alternating with the other chord tones).
            rate: Step length: "1/16" (default), "1/8", "1/8T", 0.25 ...
            start: Beat of the first chord; duration: beats per chord (default one bar).
            octaves: Range of up/down styles (1..4).  octave: octave of the root (C3 = 60) —
                use 1 or 2 for basslines.
            gate: Note length as a fraction of a step.  velocity / accent_velocity (first step of
                each chord).  swing: 0..1 of a step on every second step.
            seed: For style="random".
            clear, create, extend: As in live_clip_write_chords.

        Returns:
            {"clip", "name", "length", "added", "removed", "style", "rate",
            "chords": [{"chord", "start", "duration", "notes"}], "key"?, "seed"?, "created"?,
            "extended_to"?, "merged"?}
        """
        cmd = "notes.write_arp"
        if clip is None and track is None:
            return clip_address(track, slot, clip, cmd)
        if isinstance(chords, str):
            chords = [c for c in re.split(r"[\s|,]+", chords.strip()) if c]
        if not chords:
            return tool_error('chords must not be empty, e.g. "Am F C G"', cmd=cmd)
        if style not in _ARP_STYLES:
            return tool_error(f"style must be one of {', '.join(_ARP_STYLES)}", cmd=cmd)
        for value, label, low, high in ((octaves, "octaves", 1, 4), (octave, "octave", -1, 8),
                                        (gate, "gate", 0.05, 1.0),
                                        (velocity, "velocity", 1, 127),
                                        (swing, "swing", 0.0, 1.0)):
            if not low <= value <= high:
                return tool_error(f"{label} must be between {low} and {high}", cmd=cmd)
        if accent_velocity is not None and not 1 <= accent_velocity <= 127:
            return tool_error("accent_velocity must be 1..127", cmd=cmd)
        if duration is not None and duration <= 0:
            return tool_error("duration must be > 0 beats", cmd=cmd)
        if start < 0:
            return tool_error("start must be >= 0", cmd=cmd)
        args = address_args(track, slot, clip)
        args.update(drop_none(chords=chords, key=key, style=style, rate=rate, start=start,
                              duration=duration, octaves=octaves, octave=octave, gate=gate,
                              velocity=velocity, accent_velocity=accent_velocity, swing=swing,
                              seed=seed, clear=clear, create=create, extend=extend))
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_duplicate_notes(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float | None = None,
        end: float | None = None,
        destination: float | None = None,
        times: int = 1,
        pitch: Pitch | list[Pitch] | None = None,
        pitch_min: Pitch | None = None,
        pitch_max: Pitch | None = None,
        note_ids: list[int] | None = None,
        selected: bool = False,
        transpose: int = 0,
        transpose_steps: int = 0,
        key: str | dict[str, Any] | None = None,
        extend: bool = True,
    ) -> Any:
        """Copy a region of notes to other times in the same clip — "repeat bar 1 three times",
        "copy this phrase a fifth up to bar 3" — in one call.

        Args:
            track, slot / clip: Which clip.
            start, end: The region (note starts, beats; default the clip loop).
            destination: Where the first copy starts (default right after the region); copy k
                lands at destination + k * (end - start).
            times: Number of copies (1..64).
            pitch / pitch_min / pitch_max, note_ids, selected: Copy only these notes.
            transpose: Semitones for the copies.  transpose_steps: scale degrees in `key`
                ("song" default, "A minor", ...).
            extend: Grow the clip loop to contain the copies (default true).

        Returns:
            {"clip", "name", "length", "copied": notes per copy, "copies", "added", "at": [...],
            "note_ids"?, "extended_to"?, "merged"?, "key"?}

        Gotchas: copies replace same-pitch notes they overlap (`merged`). Chromatic copies keep
        per-note expression; scale-step copies are written as new notes.
        """
        cmd = "notes.duplicate"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if not 1 <= times <= 64:
            return tool_error("times must be 1..64", cmd=cmd)
        if start is not None and end is not None and end <= start:
            return tool_error("end must be after start", cmd=cmd)
        if not -127 <= transpose <= 127 or not -70 <= transpose_steps <= 70:
            return tool_error("transpose must be -127..127 and transpose_steps -70..70",
                              cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        low, error = _pitch_arg(pitch_min, "pitch_min", cmd)
        if error:
            return error
        high, error = _pitch_arg(pitch_max, "pitch_max", cmd)
        if error:
            return error
        args = address_args(track, slot, clip)
        args.update(drop_none(start=start, end=end, destination=destination, pitch=pitch_value,
                              pitch_min=low, pitch_max=high, note_ids=note_ids))
        if times != 1:
            args["times"] = times
        if selected:
            args["selected"] = True
        if transpose:
            args["transpose"] = transpose
        if transpose_steps:
            args["transpose_steps"] = transpose_steps
            if key is not None:
                args["key"] = key
        if not extend:
            args["extend"] = False
        return bridge_call(bridge, cmd, args)

    @mcp.tool()
    def live_clip_select_notes(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        start: float | None = None,
        end: float | None = None,
        pitch: Pitch | list[Pitch] | None = None,
        pitch_min: Pitch | None = None,
        pitch_max: Pitch | None = None,
        note_ids: list[int] | None = None,
        all: bool = False,
        none: bool = False,
        add: bool = False,
    ) -> Any:
        """Read which notes the user selected in Live's clip editor — or select notes to show
        them what you changed.

        Args:
            track, slot / clip: Which clip ("selected" = the clip open in Live's Detail view).
            start/end, pitch / pitch_min / pitch_max, note_ids: The notes to select.
            all: Select every note.  none: Deselect everything.
            add: Add to the current selection instead of replacing it.
            No filter at all = just read the selection.

        Returns:
            {"clip", "name", "length", "selected": n, "note_ids": [...]}

        Gotchas: the other note tools accept `selected=true` to work on the user's selection
        ("transpose the notes I selected").
        """
        cmd = "notes.select"
        error = clip_address(track, slot, clip, cmd)
        if error:
            return error
        if all and none:
            return tool_error("pass all or none, not both", cmd=cmd)
        pitch_value, error = _pitch_arg(pitch, "pitch", cmd)
        if error:
            return error
        low, error = _pitch_arg(pitch_min, "pitch_min", cmd)
        if error:
            return error
        high, error = _pitch_arg(pitch_max, "pitch_max", cmd)
        if error:
            return error
        args = address_args(track, slot, clip)
        args.update(drop_none(start=start, end=end, pitch=pitch_value, pitch_min=low,
                              pitch_max=high, note_ids=note_ids))
        if all:
            args["all"] = True
        if none:
            args["none"] = True
        if add:
            args["add"] = True
        return bridge_call(bridge, cmd, args)
