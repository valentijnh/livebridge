"""MIDI notes: read (paged, compact), add, replace, clear, modify by id,
transform (quantize / humanize / transpose / velocity / legato), and two
composers — ``notes.write_pattern`` (step-sequencer strings) and
``notes.write_chords`` (chord symbols) — backed by a small self-contained
music-theory helper (no dependencies).

Clip addressing is shared with ``clips.*``: ``track`` + ``slot`` or ``clip``
(path / name / "selected"); arrangement clips work through their path.

Compact note format (``notes.get``)::

    [pitch, start, duration, velocity, mute, probability, note_id]

Pitches are MIDI numbers 0..127 or names in Live's convention — **C3 = 60**
(so C1 = 36 = the first Drum Rack pad, C-2 = 0, G8 = 127); sharps "F#4",
flats "Db2".  General-MIDI drum names ("kick", "snare", "hat", ...) are
accepted where a pitch is expected.  Times and durations are beats.

Uses the Live 11+ note API only (``add_new_notes``,
``get_all_notes_extended``, ``apply_note_modifications``,
``remove_notes_by_id``) so probability, velocity deviation and per-note
expression are preserved.
"""

import bisect
import math
import random
import re

from .. import compat
from ..registry import BridgeError, command
from .clips import (as_bool, as_float, as_int, beats_per_bar, clip_region, extend_clip_to,
                    live_call, midi_clip_for_writing, owner_track, resolve_clip, rnd)


# ==========================================================================
# music theory: note names, drum names, grids, chords
# ==========================================================================

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"^([A-Ga-g])([#b♯♭]*)(-?\d+)$")

#: General-MIDI drum map (= Live's Drum Rack pad layout: C1 = 36 = kick).
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


def note_to_midi(value, what="pitch"):
    """A MIDI note number from an int, a numeric string, a note name (Live's
    convention, C3 = 60) or a GM drum name.

    Raises:
        BridgeError: ``bad_args`` with a hint when the value is not a note.
    """
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be 0..127 or a note name, got %r" % (what, value))
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if 0 <= value <= 127:
            return value
        raise BridgeError("bad_args", "%s %d is outside the MIDI range 0..127" % (what, value))
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return note_to_midi(int(text), what)
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
            raise BridgeError("bad_args", "%s %r is outside the MIDI range (C-2..G8)"
                              % (what, value))
    raise BridgeError("bad_args", "%s %r is not a note — use 0..127, a name like C3 (=60), "
                      "F#4, Db2, or a drum name like kick/snare/hat" % (what, value))


FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")


def midi_to_note(pitch, flats=False):
    """``60`` -> ``"C3"`` (Live's convention); ``flats`` spells 70 as "Bb3", else "A#3"."""
    pitch = int(pitch)
    return "%s%d" % ((FLAT_NAMES if flats else NOTE_NAMES)[pitch % 12], pitch // 12 - 2)


# --------------------------------------------------------------------------
# keys and scales
# --------------------------------------------------------------------------

#: Scale names -> intervals: Live 12's own list comes from Live.Song.get_all_scales_ordered();
#: this table is the fallback (and adds common aliases).
SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10), "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "natural minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10), "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10), "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic minor": (0, 2, 3, 5, 7, 9, 11), "major pentatonic": (0, 2, 4, 7, 9),
    "minor pentatonic": (0, 3, 5, 7, 10), "pentatonic": (0, 2, 4, 7, 9),
    "minor blues": (0, 3, 5, 6, 7, 10), "blues": (0, 3, 5, 6, 7, 10),
    "whole tone": (0, 2, 4, 6, 8, 10), "chromatic": tuple(range(12)),
    "harmonic major": (0, 2, 4, 5, 7, 8, 11), "phrygian dominant": (0, 1, 4, 5, 7, 8, 10),
    "hungarian minor": (0, 2, 3, 6, 7, 8, 11),
}
_MAJOR = SCALES["major"]
#: Keys whose signature has flats (major-type and minor-type roots).
_FLAT_MAJOR_ROOTS = {5, 10, 3, 8, 1, 6}
_FLAT_MINOR_ROOTS = {2, 7, 0, 5, 10, 3}
_KEY_RE = re.compile(r"^([A-Ga-g])([#b♯♭]?)(m(?![a-z]))?\s*(.*)$")


class Key(object):
    """A root pitch class + scale intervals (+ a spelling preference)."""

    def __init__(self, root, intervals, name, flats=None):
        self.root = int(root) % 12
        self.intervals = tuple(sorted(set(int(i) % 12 for i in intervals))) or _MAJOR
        if 0 not in self.intervals:
            self.intervals = (0,) + self.intervals
        self.name = name
        if flats is None:
            minor = 3 in self.intervals and 4 not in self.intervals
            flats = self.root in (_FLAT_MINOR_ROOTS if minor else _FLAT_MAJOR_ROOTS)
        self.flats = bool(flats)

    def label(self):
        return "%s %s" % ((FLAT_NAMES if self.flats else NOTE_NAMES)[self.root], self.name)

    def pitch_classes(self):
        return set((self.root + i) % 12 for i in self.intervals)

    def contains(self, pitch):
        return (int(pitch) - self.root) % 12 in self.intervals

    def degree_of(self, pitch):
        """``(octave, index, chromatic offset)`` of a pitch against the scale (below/at)."""
        relative = int(pitch) - self.root
        octave, pc = divmod(relative, 12)
        index = max(i for i, v in enumerate(self.intervals) if v <= pc)
        return octave, index, pc - self.intervals[index]

    def pitch_of(self, octave, index, offset=0):
        count = len(self.intervals)
        octave += index // count
        index %= count
        return self.root + octave * 12 + self.intervals[index] + offset

    def step(self, pitch, steps):
        """Move ``pitch`` by ``steps`` scale degrees (a chromatic note keeps its offset)."""
        octave, index, offset = self.degree_of(pitch)
        return self.pitch_of(octave, index + int(steps), offset)

    def snap(self, pitch, direction="nearest"):
        """The scale pitch nearest to ``pitch`` ("nearest" prefers the lower on a tie)."""
        if self.contains(pitch):
            return int(pitch)
        down = up = int(pitch)
        while not self.contains(down):
            down -= 1
        while not self.contains(up):
            up += 1
        if direction == "down":
            return down
        if direction == "up":
            return up
        return down if pitch - down <= up - pitch else up

    def as_dict(self):
        return {"key": self.label(), "root": (FLAT_NAMES if self.flats else
                                              NOTE_NAMES)[self.root],
                "root_note": self.root, "scale": self.name, "intervals": list(self.intervals)}


def _live_scales():
    try:
        import Live
        pairs = Live.Song.get_all_scales_ordered()
        return dict((str(name).lower(), tuple(int(i) for i in intervals))
                    for name, intervals in pairs)
    except Exception:
        return {}


def scale_intervals(name):
    """Intervals of a scale name (Live's list first, then the built-in table)."""
    wanted = re.sub(r"\s+", " ", str(name).strip().lower())
    table = dict(SCALES)
    table.update(_live_scales())
    if wanted in table:
        return table[wanted], wanted
    for key in sorted(table):
        if key.startswith(wanted):
            return table[key], key
    raise BridgeError("bad_args", "unknown scale %r — e.g. major, minor, dorian, mixolydian, "
                      "harmonic minor, minor pentatonic, blues" % (name,))


def song_key(song):
    """The song's key (Live 12 root_note / scale_name / scale_intervals)."""
    get = compat.safe_getattr
    root = get(song, "root_note", 0) or 0
    intervals = get(song, "scale_intervals")
    name = str(get(song, "scale_name", "Major") or "Major")
    try:
        intervals = tuple(int(i) for i in intervals) if intervals is not None else None
    except TypeError:
        intervals = None
    if not intervals:
        intervals = scale_intervals(name)[0] if name else _MAJOR
    return Key(root, intervals, name)


def parse_key(ctx, key):
    """A :class:`Key` from ``None``/"song" (the song's scale), "A minor", "F# dorian", "Bb",
    "Am", or {"root": "A"|9, "scale": "minor"} / {"root": .., "intervals": [...]}."""
    if key is None or (isinstance(key, str) and key.strip().lower() in ("song", "")):
        return song_key(ctx.song)
    if isinstance(key, dict):
        root = key.get("root", key.get("root_note"))
        if isinstance(root, str):
            match = _KEY_RE.match(root.strip())
            if not match:
                raise BridgeError("bad_args", "key root %r is not a note name" % (root,))
            flats = match.group(2) in ("b", "♭") or None
            root_pc = _pitch_class(match.group(1), match.group(2))
        elif isinstance(root, int) and not isinstance(root, bool) and 0 <= root <= 11:
            root_pc, flats = root, None
        else:
            raise BridgeError("bad_args", "key needs root (a note name or 0..11)")
        if "intervals" in key:
            intervals = key["intervals"]
            if not isinstance(intervals, (list, tuple)) or not intervals:
                raise BridgeError("bad_args", "key intervals must be a list of semitones")
            return Key(root_pc, [as_int(i, "key.intervals", 0, 11) for i in intervals],
                       str(key.get("scale", "custom")), flats)
        intervals, name = scale_intervals(key.get("scale", "major"))
        return Key(root_pc, intervals, name, flats)
    if not isinstance(key, str):
        raise BridgeError("bad_args", "key must be \"song\", a string like \"A minor\" or "
                          "{root, scale}")
    match = _KEY_RE.match(key.strip())
    if not match:
        raise BridgeError("bad_args", "key %r: start with the root, e.g. \"A minor\", "
                          "\"F# dorian\", \"Bb\", \"Am\"" % key)
    letter, accidental, minor_suffix, rest = match.groups()
    root_pc = _pitch_class(letter, accidental)
    flats = True if accidental in ("b", "♭") else (False if accidental in ("#", "♯")
                                                   else None)
    if rest.strip():
        intervals, name = scale_intervals(rest)
    elif minor_suffix:
        intervals, name = SCALES["minor"], "minor"
    else:
        intervals, name = _MAJOR, "major"
    return Key(root_pc, intervals, name, flats)


def parse_grid(value, what="grid"):
    """A grid in beats from a number (beats) or a note value.

    "1/4" = 1 beat, "1/8" = 0.5, "1/16" = 0.25, "1/32"; suffix "T" = triplet
    (x 2/3), "." = dotted (x 1.5); "1/1" or "bar" = 4 beats.
    """
    if isinstance(value, bool):
        raise BridgeError("bad_args", "%s must be beats or a note value like '1/16'" % what)
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().lower().replace(" ", "")
        if text in ("bar", "1bar"):
            return 4.0
        match = re.match(r"^1/([1-9]\d*)(t|\.)?$", text)
        if match:
            number = 4.0 / int(match.group(1))
            if match.group(2) == "t":
                number *= 2.0 / 3.0
            elif match.group(2) == ".":
                number *= 1.5
        else:
            try:
                number = float(text)
            except ValueError:
                raise BridgeError("bad_args", "%s %r is not a grid — use beats (0.25) or a note "
                                  "value like '1/16', '1/8T', '1/4.'" % (what, value))
    else:
        raise BridgeError("bad_args", "%s must be beats or a note value like '1/16'" % what)
    if not number > 0 or number > 64:
        raise BridgeError("bad_args", "%s must be between 0 and 64 beats" % what)
    return number


#: Chord qualities -> semitone intervals above the root.
_QUALITIES = {
    "": (0, 4, 7), "maj": (0, 4, 7), "M": (0, 4, 7), "major": (0, 4, 7),
    "m": (0, 3, 7), "min": (0, 3, 7), "-": (0, 3, 7), "minor": (0, 3, 7),
    "dim": (0, 3, 6), "°": (0, 3, 6), "o": (0, 3, 6),
    "aug": (0, 4, 8), "+": (0, 4, 8),
    "sus2": (0, 2, 7), "sus4": (0, 5, 7), "sus": (0, 5, 7), "5": (0, 7),
    "6": (0, 4, 7, 9), "maj6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "min6": (0, 3, 7, 9),
    "69": (0, 4, 7, 9, 14), "6/9": (0, 4, 7, 9, 14), "m69": (0, 3, 7, 9, 14),
    "7": (0, 4, 7, 10), "dom7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11), "M7": (0, 4, 7, 11), "ma7": (0, 4, 7, 11),
    "Δ": (0, 4, 7, 11), "Δ7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "-7": (0, 3, 7, 10),
    "mmaj7": (0, 3, 7, 11), "mM7": (0, 3, 7, 11), "m(maj7)": (0, 3, 7, 11),
    "minmaj7": (0, 3, 7, 11),
    "m7b5": (0, 3, 6, 10), "min7b5": (0, 3, 6, 10), "-7b5": (0, 3, 6, 10),
    "ø": (0, 3, 6, 10), "ø7": (0, 3, 6, 10), "halfdim": (0, 3, 6, 10),
    "dim7": (0, 3, 6, 9), "°7": (0, 3, 6, 9), "o7": (0, 3, 6, 9),
    "aug7": (0, 4, 8, 10), "+7": (0, 4, 8, 10), "7#5": (0, 4, 8, 10),
    "augmaj7": (0, 4, 8, 11), "maj7#5": (0, 4, 8, 11), "+maj7": (0, 4, 8, 11),
    "7sus4": (0, 5, 7, 10), "7sus": (0, 5, 7, 10), "7sus2": (0, 2, 7, 10),
    "9": (0, 4, 7, 10, 14), "maj9": (0, 4, 7, 11, 14), "M9": (0, 4, 7, 11, 14),
    "Δ9": (0, 4, 7, 11, 14), "m9": (0, 3, 7, 10, 14), "min9": (0, 3, 7, 10, 14),
    "-9": (0, 3, 7, 10, 14), "9sus4": (0, 5, 7, 10, 14),
    "add9": (0, 4, 7, 14), "add2": (0, 2, 4, 7), "madd9": (0, 3, 7, 14),
    "m(add9)": (0, 3, 7, 14), "add11": (0, 4, 7, 17), "add4": (0, 4, 5, 7),
    "11": (0, 4, 7, 10, 14, 17), "m11": (0, 3, 7, 10, 14, 17),
    "min11": (0, 3, 7, 10, 14, 17), "maj11": (0, 4, 7, 11, 14, 17),
    "13": (0, 4, 7, 10, 14, 21), "maj13": (0, 4, 7, 11, 14, 21),
    "m13": (0, 3, 7, 10, 14, 21), "min13": (0, 3, 7, 10, 14, 21),
    "7b9": (0, 4, 7, 10, 13), "7#9": (0, 4, 7, 10, 15), "7b5": (0, 4, 6, 10),
    "7#11": (0, 4, 7, 10, 18), "7b13": (0, 4, 7, 10, 20), "7alt": (0, 4, 8, 10, 13),
}
_QUALITY_KEYS = sorted(_QUALITIES, key=len, reverse=True)
_MODIFIER_RE = re.compile(r"^(add[b#]?\d+|sus[24]|no[35]|omit[35]|[b#]\d+|[(),])")
_ROOT_RE = re.compile(r"^([A-Ga-g])([#b♯♭]?)")
_REST_SYMBOLS = ("n.c.", "nc", "n.c", "-", "rest", "r", "x", "%")

#: Interval for an extension degree ("9" -> 14).
_DEGREES = {2: 2, 3: 4, 4: 5, 5: 7, 6: 9, 7: 10, 9: 14, 11: 17, 13: 21}


def _pitch_class(letter, accidental):
    value = _PITCH_CLASS[letter.upper()]
    if accidental in ("#", "♯"):
        value += 1
    elif accidental in ("b", "♭"):
        value -= 1
    return value % 12


def parse_chord(symbol):
    """Parse a chord symbol into ``(root_pc, intervals, bass_pc_or_None)``.

    Supports triads (C, Cm, Cdim, Caug, Csus2/4, C5), sixths, sevenths
    (7, maj7, m7, m7b5, dim7, mMaj7), extensions (9, 11, 13, add9, 6/9),
    alterations (b5 #5 b9 #9 #11 b13), no3/no5 and slash bass ("G/B").
    ``None`` for a rest ("N.C.").
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise BridgeError("bad_args", "chord symbol must be a non-empty string")
    text = symbol.strip()
    if text.lower() in _REST_SYMBOLS:
        return None
    bass = None
    if "/" in text:
        head, tail = text.rsplit("/", 1)
        tail_match = re.match(r"^([A-Ga-g])([#b♯♭]?)$", tail.strip())
        if tail_match and head:
            bass = _pitch_class(tail_match.group(1), tail_match.group(2))
            text = head.strip()
    match = _ROOT_RE.match(text)
    if not match:
        raise BridgeError("bad_args", "chord %r: must start with a root note A-G" % symbol)
    root = _pitch_class(match.group(1), match.group(2))
    rest = text[match.end():]
    quality = None
    lowered = rest.lower()
    for key in _QUALITY_KEYS:
        # "M7" vs "m7" is case-sensitive; spelled-out qualities ("Maj7",
        # "mMaj7", "Min") are not.
        if rest.startswith(key) or (len(key) >= 3 and lowered.startswith(key.lower())):
            quality = key
            break
    intervals = set(_QUALITIES[quality or ""])
    rest = rest[len(quality or ""):]
    while rest:
        mod = _MODIFIER_RE.match(rest)
        if not mod:
            raise BridgeError("bad_args", "chord %r: cannot understand %r (try e.g. Am7, F, "
                              "G/B, Cmaj9, Dm7b5, E7#9, Csus4, N.C.)" % (symbol, rest))
        token = mod.group(1)
        rest = rest[len(token):]
        if token in "(),":
            continue
        if token.startswith("sus"):
            intervals -= {3, 4}
            intervals.add(2 if token == "sus2" else 5)
        elif token.startswith("no") or token.startswith("omit"):
            if token.endswith("3"):
                intervals -= {3, 4}
            else:
                intervals -= {6, 7, 8}
        elif token.startswith("add"):
            accidental = token[3] if token[3] in "b#" else ""
            degree = int(token[3 + len(accidental):])
            if degree not in _DEGREES:
                raise BridgeError("bad_args", "chord %r: cannot add degree %d" % (symbol, degree))
            step = _DEGREES[degree] + (1 if accidental == "#" else -1 if accidental == "b" else 0)
            intervals.add(step)
        else:
            accidental, degree = token[0], int(token[1:])
            if degree not in _DEGREES:
                raise BridgeError("bad_args", "chord %r: unknown alteration %r" % (symbol, token))
            base = _DEGREES[degree]
            shifted = base + (1 if accidental == "#" else -1)
            if degree == 5:
                intervals -= {7}
            intervals.add(shifted)
    return root, tuple(sorted(intervals)), bass


_ROMAN_RE = re.compile(r"^([b#♭♯]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(.*)$")
_ROMAN_VALUES = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}
_DEGREE_RE = re.compile(r"^([b#♭♯]?)([1-7])(7|9)?$")


def _accidental_flats(accidental, key):
    if accidental in ("b", "♭"):
        return True
    if accidental in ("#", "♯"):
        return False
    return key.flats


def _degree_root(key, index, accidental):
    intervals = key.intervals if len(key.intervals) == 7 else _MAJOR
    shift = 1 if accidental in ("#", "♯") else -1 if accidental in ("b", "♭") else 0
    return (key.root + intervals[index] + shift) % 12


def _roman_quality(numeral, suffix):
    lower = numeral == numeral.lower()
    text = suffix.strip()
    for mark in ("°", "o", "dim"):
        if text.startswith(mark):
            rest = text[len(mark):]
            return "dim7" if rest == "7" else "dim" + rest
    if text.startswith("ø"):
        return "m7b5"
    if text.startswith("+") or text.startswith("aug"):
        return text if text.startswith("aug") else "aug" + text[1:]
    if lower and not text.startswith("m"):
        return "m" + text
    if lower and text.startswith("maj"):
        return "m" + text
    return text


def chord_from_symbol(symbol, key_getter):
    """``(root_pc, intervals, bass_pc, flats)`` for a chord symbol ("Am7", "G/B"), a Roman
    numeral in the key ("vi", "V7", "IVmaj7", "ii°", "bVII") or a scale degree ("1", "5",
    "57" = the diatonic 7th chord on degree 5). ``None`` for a rest.

    ``key_getter()`` returns the :class:`Key` (called only for numerals/degrees).
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise BridgeError("bad_args", "chord symbol must be a non-empty string")
    text = symbol.strip()
    if text.lower() in _REST_SYMBOLS:
        return None
    degree = _DEGREE_RE.match(text)
    if degree:
        key = key_getter()
        accidental, number, extension = degree.groups()
        index = int(number) - 1
        scale = key.intervals if len(key.intervals) == 7 else _MAJOR
        count = len(scale)
        stack = [0, 2, 4] + ([6] if extension == "7" else [6, 8] if extension == "9" else [])
        base = scale[index]
        intervals = []
        for offset in stack:
            position = index + offset
            value = scale[position % count] + 12 * (position // count) - base
            intervals.append(value)
        root = _degree_root(key, index, accidental)
        return root, tuple(sorted(intervals)), None, _accidental_flats(accidental, key)
    roman = _ROMAN_RE.match(text)
    if roman:
        accidental, numeral, suffix = roman.groups()
        if "/" in suffix:
            raise BridgeError("bad_args", "chord %r: slash or secondary chords are not "
                              "supported with Roman numerals — write the chord symbol "
                              "(e.g. D7 for V/V in C)" % symbol)
        key = key_getter()
        root = _degree_root(key, _ROMAN_VALUES[numeral.lower()], accidental)
        quality = _roman_quality(numeral, suffix)
        flats = _accidental_flats(accidental, key)
        name = (FLAT_NAMES if flats else NOTE_NAMES)[root]
        try:
            parsed = parse_chord(name + quality)
        except BridgeError:
            raise BridgeError("bad_args", "chord %r: cannot understand the quality %r (try "
                              "I, vi, V7, IVmaj7, ii7, vii°, iiø7, bVII)" % (symbol, suffix))
        return parsed[0], parsed[1], parsed[2], flats
    parsed = parse_chord(text)
    if parsed is None:
        return None
    accidental = _ROOT_RE.match(text).group(2)
    return parsed[0], parsed[1], parsed[2], accidental in ("b", "♭")


def voice_chord(root_pc, intervals, octave=3, inversion=0, voicing="close"):
    """Concrete MIDI pitches for a parsed chord (root in ``octave``, C3 = 60)."""
    base = (int(octave) + 2) * 12 + root_pc
    notes = sorted(base + interval for interval in intervals)
    for _ in range(int(inversion) % max(1, len(notes))):
        notes = notes[1:] + [notes[0] + 12]
    return revoice(sorted(notes), voicing)


def revoice(pitches, voicing):
    """Re-voice a close-position chord.

    ``open``: every second note up an octave; ``drop2`` / ``drop3``: the
    second / third note from the top down an octave; ``spread``: the lowest
    note down an octave and the rest opened up.
    """
    notes = sorted(pitches)
    if voicing == "open" and len(notes) >= 3:
        notes = [n + 12 if index % 2 == 1 else n for index, n in enumerate(notes)]
    elif voicing == "drop2" and len(notes) >= 3:
        notes[-2] -= 12
    elif voicing == "drop3" and len(notes) >= 4:
        notes[-3] -= 12
    elif voicing == "spread" and len(notes) >= 3:
        upper = notes[1:]
        notes = [notes[0] - 12] + [n + 12 if index % 2 == 1 else n
                                   for index, n in enumerate(upper)]
    return sorted(notes)


def _closest_voicing(root_pc, intervals, octave, previous):
    """The inversion/octave of the chord that moves least from ``previous``."""
    best, best_cost = None, None
    count = max(1, len(intervals))
    center = sum(previous) / float(len(previous))
    for shift in (-1, 0, 1):
        for inversion in range(count):
            candidate = voice_chord(root_pc, intervals, octave + shift, inversion, "close")
            if candidate[0] < 12 or candidate[-1] > 115:
                continue
            cost = 0.0
            for note in candidate:
                cost += min(abs(note - p) for p in previous)
            cost += abs(sum(candidate) / float(len(candidate)) - center) * 0.5
            if best_cost is None or cost < best_cost:
                best, best_cost = candidate, cost
    return best or voice_chord(root_pc, intervals, octave, 0, "close")


# ==========================================================================
# note helpers
# ==========================================================================

FIELDS = ["pitch", "start", "duration", "velocity", "mute", "probability", "note_id"]
_FIELD_EXPR = ["velocity_deviation", "release_velocity"]


def _all_notes(clip):
    """A ``MidiNoteVector`` with every note of the clip (Live objects)."""
    if compat.has(clip, "get_all_notes_extended"):
        return live_call("get_all_notes_extended", clip.get_all_notes_extended)
    if not compat.has(clip, "get_notes_extended"):
        raise BridgeError("unsupported", "this Live version has no extended note API "
                          "(needs Live 11+)")
    start = min(0.0, compat.safe_getattr(clip, "start_marker", 0.0) or 0.0,
                compat.safe_getattr(clip, "loop_start", 0.0) or 0.0)
    span = max(compat.safe_getattr(clip, "end_marker", 0.0) or 0.0,
               compat.safe_getattr(clip, "loop_end", 0.0) or 0.0) - start + 100000.0
    return live_call("get_notes_extended", clip.get_notes_extended, 0, 128, start, span)


def _pitch_bounds(pitch, pitch_min, pitch_max):
    if pitch is not None:
        if isinstance(pitch, (list, tuple)):
            return set(note_to_midi(p) for p in pitch), 0, 127
        value = note_to_midi(pitch)
        return None, value, value
    low = note_to_midi(pitch_min, "pitch_min") if pitch_min is not None else 0
    high = note_to_midi(pitch_max, "pitch_max") if pitch_max is not None else 127
    if low > high:
        raise BridgeError("bad_args", "pitch_min must not be above pitch_max")
    return None, low, high


def _select(notes, start=None, end=None, pitch=None, pitch_min=None, pitch_max=None,
            note_ids=None):
    """The notes (live objects from ``notes``) matching the filters.

    ``start``/``end`` filter on the note start (``start <= t < end``).
    """
    start = as_float(start, "start", allow_none=True)
    end = as_float(end, "end", allow_none=True)
    if start is not None and end is not None and end <= start:
        raise BridgeError("bad_args", "end must be after start")
    pitch_set, low, high = _pitch_bounds(pitch, pitch_min, pitch_max)
    ids = None
    if note_ids is not None:
        if not isinstance(note_ids, (list, tuple)):
            raise BridgeError("bad_args", "note_ids must be a list of note ids")
        ids = set(as_int(i, "note_id") for i in note_ids)
    chosen = []
    for note in notes:
        if ids is not None and int(note.note_id) not in ids:
            continue
        if pitch_set is not None:
            if note.pitch not in pitch_set:
                continue
        elif not (low <= note.pitch <= high):
            continue
        if start is not None and note.start_time < start - 1e-9:
            continue
        if end is not None and note.start_time >= end - 1e-9:
            continue
        chosen.append(note)
    return chosen


def _selected_ids(clip):
    """Note ids of the notes selected in Live's clip editor."""
    if not compat.has(clip, "get_selected_notes_extended"):
        raise BridgeError("unsupported", "this Live version cannot read the note selection")
    return [int(n.note_id) for n in live_call("get_selected_notes_extended",
                                              clip.get_selected_notes_extended)]


def _selection_ids(clip, note_ids, selected):
    """``note_ids`` merged with Live's selection when ``selected`` is true."""
    if not as_bool(selected, "selected"):
        return note_ids
    chosen = _selected_ids(clip)
    if note_ids is not None:
        wanted = set(as_int(i, "note_id") for i in note_ids)
        chosen = [i for i in chosen if i in wanted]
    return chosen


def _row(note, expression=False, names=False):
    velocity = rnd(note.velocity, 2)
    row = [midi_to_note(note.pitch) if names else int(note.pitch), rnd(note.start_time),
           rnd(note.duration), velocity, bool(note.mute), rnd(note.probability, 3),
           int(note.note_id)]
    if expression:
        row.append(rnd(compat.safe_getattr(note, "velocity_deviation", 0.0), 2))
        row.append(rnd(compat.safe_getattr(note, "release_velocity", 64.0), 2))
    return row


def _spec_from(item, index):
    """Normalise one note given as a dict or a list into keyword arguments."""
    if isinstance(item, dict):
        unknown = set(item) - {"pitch", "start", "start_time", "time", "duration", "length",
                               "velocity", "mute", "probability", "velocity_deviation",
                               "release_velocity"}
        if unknown:
            raise BridgeError("bad_args", "notes[%d]: unknown field(s) %s" %
                              (index, ", ".join(sorted(unknown))))
        pitch = item.get("pitch")
        start = item.get("start", item.get("start_time", item.get("time")))
        duration = item.get("duration", item.get("length"))
        velocity = item.get("velocity", 100)
        mute = item.get("mute", False)
        probability = item.get("probability", 1.0)
        deviation = item.get("velocity_deviation", 0.0)
        release = item.get("release_velocity", 64.0)
    elif isinstance(item, (list, tuple)):
        if not 3 <= len(item) <= 6:
            raise BridgeError("bad_args", "notes[%d]: a list note is [pitch, start, duration, "
                              "velocity?, mute?, probability?]" % index)
        padded = list(item) + [None] * (6 - len(item))
        pitch, start, duration = padded[0], padded[1], padded[2]
        velocity = 100 if padded[3] is None else padded[3]
        mute = False if padded[4] is None else padded[4]
        probability = 1.0 if padded[5] is None else padded[5]
        deviation, release = 0.0, 64.0
    else:
        raise BridgeError("bad_args", "notes[%d] must be an object or a list" % index)
    if pitch is None or start is None or duration is None:
        raise BridgeError("bad_args", "notes[%d] needs pitch, start and duration" % index)
    where = "notes[%d]" % index
    spec = {
        "pitch": note_to_midi(pitch, where + ".pitch"),
        "start_time": as_float(start, where + ".start", minimum=0.0),
        "duration": as_float(duration, where + ".duration"),
        "velocity": as_float(velocity, where + ".velocity", 0.0, 127.0),
        "mute": as_bool(mute, where + ".mute"),
        "probability": as_float(probability, where + ".probability", 0.0, 1.0),
        "velocity_deviation": as_float(deviation, where + ".velocity_deviation",
                                       -127.0, 127.0),
        "release_velocity": as_float(release, where + ".release_velocity", 0.0, 127.0),
    }
    if spec["duration"] <= 0:
        raise BridgeError("bad_args", "%s.duration must be > 0" % where)
    spec["velocity"] = max(1.0, spec["velocity"])
    return spec


def _add_specs(clip, specs):
    """Add normalised note dicts; returns the new note ids."""
    if not specs:
        return []
    if not compat.has(clip, "add_new_notes"):
        raise BridgeError("unsupported", "this Live version has no add_new_notes (Live 11+)")
    import Live  # only available inside Live (or the test stub)
    note_spec = Live.Clip.MidiNoteSpecification
    objects = []
    for spec in specs:
        objects.append(note_spec(pitch=int(spec["pitch"]), start_time=float(spec["start_time"]),
                                 duration=float(spec["duration"]),
                                 velocity=float(spec["velocity"]), mute=bool(spec["mute"]),
                                 probability=float(spec["probability"]),
                                 velocity_deviation=float(spec["velocity_deviation"]),
                                 release_velocity=float(spec["release_velocity"])))
    ids = live_call("add_new_notes", clip.add_new_notes, tuple(objects))
    try:
        return [int(i) for i in ids]
    except TypeError:
        return []


def _note_count(clip):
    """Number of notes in the clip (``None`` when it cannot be read)."""
    try:
        return len(_all_notes(clip))
    except (BridgeError, TypeError):
        return None


def _write_notes(clip, specs):
    """Add ``specs`` and report what Live kept: ``(ids, added, merged)``.

    Live 12.4.5 never keeps two notes of the same pitch that overlap
    (verified on the running Live): a new note replaces an existing note (or
    an earlier note of the same batch) that starts at the same pitch and
    time, removes same-pitch notes that start inside it, and shortens a
    same-pitch note that it starts inside of.  ``merged`` is the number of
    notes that disappeared that way (0 when none).
    """
    before = _note_count(clip)
    ids = _add_specs(clip, specs)
    after = _note_count(clip)
    merged = 0
    if before is not None and after is not None:
        merged = max(0, before + len(specs) - after)
    added = len(ids) if ids else len(specs) - merged
    return ids, added, merged


def _remove(clip, notes):
    """Remove the given live note objects; returns how many."""
    if not notes:
        return 0
    if compat.has(clip, "remove_notes_by_id"):
        live_call("remove_notes_by_id", clip.remove_notes_by_id,
                  [int(n.note_id) for n in notes])
        return len(notes)
    for note in notes:
        live_call("remove_notes_extended", clip.remove_notes_extended, int(note.pitch), 1,
                  float(note.start_time), 1e-6)
    return len(notes)


def _apply(clip, vector):
    live_call("apply_note_modifications", clip.apply_note_modifications, vector)


def _clip_end(clip):
    """End of what the clip plays (loop end, or the clip end when unlooped)."""
    return clip_region(clip)[1]


def _timeline_hint(clip, result):
    """Warn when an arrangement clip's loop now reaches past its timeline end."""
    if not compat.safe_getattr(clip, "is_arrangement_clip", False) or \
            not compat.safe_getattr(clip, "looping", False):
        return
    start = compat.safe_getattr(clip, "start_time")
    end = compat.safe_getattr(clip, "end_time")
    if start is None or end is None:
        return
    shown = float(end) - float(start)
    if shown + 1e-9 < float(compat.safe_getattr(clip, "length", 0.0) or 0.0):
        result["timeline_end"] = rnd(end)
        result["note"] = ("the loop grew but a looped arrangement clip keeps its length on the "
                          "timeline (%s beats) and repeats its loop inside it; to show more, "
                          "clips.set looping=false end_marker=<beats>, then looping=true"
                          % rnd(shown))


def _summary(ctx, clip):
    return {"clip": ctx.path_of(clip), "name": compat.safe_getattr(clip, "name"),
            "length": rnd(compat.safe_getattr(clip, "length", 0.0))}


# ==========================================================================
# commands: read / write
# ==========================================================================

@command("notes.get", doc="Read MIDI notes compactly, with pitch/time filters and paging")
def notes_get(ctx, track=None, slot=None, clip=None, start=None, end=None, pitch=None,
              pitch_min=None, pitch_max=None, offset=0, limit=512, include_expression=False,
              pitch_names=False, selected=False):
    """Read the notes of a MIDI clip.

    Args:
        track, slot / clip: which clip (session address, path, name or "selected").
        start, end: only notes whose start lies in [start, end) beats (clip time).
        pitch: one pitch or a list of pitches (numbers or names like "C3", "kick").
        pitch_min, pitch_max: pitch range (inclusive) when ``pitch`` is omitted.
        offset, limit: paging (limit max 2000), sorted by start then pitch.
        include_expression: append velocity_deviation and release_velocity.
        pitch_names: pitches as names instead of numbers — Live's own spelling
            for the clip (``Clip.note_number_to_name``: follows the song key,
            e.g. "A♯2"/"B♭2"), else sharps ("A#2").
        selected: only the notes selected in Live's clip editor.

    Returns:
        {"clip": path, "name", "length", "loop": [start, end], "total", "count",
         "offset", "fields": [...], "notes": [[pitch, start, duration, velocity,
         mute, probability, note_id], ...], "next_offset"?}

    Gotchas:
        Times are clip-relative beats, including notes outside the loop.
        ``note_id`` is what notes.modify / notes.clear / notes.transform take.
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    include_expression = as_bool(include_expression, "include_expression")
    pitch_names = as_bool(pitch_names, "pitch_names")
    offset = as_int(offset, "offset", minimum=0)
    limit = as_int(limit, "limit", minimum=1, maximum=2000)
    chosen = _select(_all_notes(obj), start, end, pitch, pitch_min, pitch_max,
                     _selection_ids(obj, None, selected))
    chosen.sort(key=lambda n: (n.start_time, n.pitch))
    window = chosen[offset:offset + limit]
    result = _summary(ctx, obj)
    result.update({
        "loop": [rnd(compat.safe_getattr(obj, "loop_start", 0.0)),
                 rnd(compat.safe_getattr(obj, "loop_end", 0.0))],
        "total": len(chosen),
        "offset": offset,
        "count": len(window),
        "fields": FIELDS + (_FIELD_EXPR if include_expression else []),
        "notes": [_row(n, include_expression, pitch_names) for n in window],
    })
    if pitch_names and compat.has(obj, "note_number_to_name"):
        for row in result["notes"]:
            ok, name = compat.safe_call(obj, "note_number_to_name", note_to_midi(row[0]))
            if ok and name:
                row[0] = str(name)
    if offset + len(window) < len(chosen):
        result["next_offset"] = offset + len(window)
    return result


@command("notes.add", mutating=True, doc="Add MIDI notes to a clip (names like C3 allowed)")
def notes_add(ctx, track=None, slot=None, clip=None, notes=None, create=False, extend=False):
    """Add notes.

    Args:
        track, slot / clip: which clip.
        notes: list of {"pitch": 60|"C3", "start": beats, "duration": beats,
            "velocity": 1..127 (default 100), "mute": false, "probability": 0..1,
            "velocity_deviation": -127..127, "release_velocity": 0..127} — or
            lists [pitch, start, duration, velocity?, mute?, probability?].
        create: when the slot is empty, create a MIDI clip long enough for the
            notes (whole bars). With create and no slot, the track's first empty
            slot is used.
        extend: grow the clip loop (whole bars) when notes end after it.

    Returns:
        {"clip": path, "name", "length", "added": n, "note_ids": [...],
         "created"?: true, "extended_to"?: beats, "merged"?: n,
         "timeline_end"?, "note"?} — the last two when a looped arrangement
         clip's loop now outgrows its (fixed) length on the timeline.

    Gotchas:
        Live never keeps two overlapping notes of the same pitch: a new note
        replaces one that starts at the same pitch and time, swallows
        same-pitch notes that start inside it and shortens one it starts
        inside of. ``merged`` counts the notes that disappeared that way
        (``added``/``note_ids`` are the notes Live kept). Use notes.replace
        for "rewrite this region".
    """
    if not isinstance(notes, (list, tuple)) or not notes:
        raise BridgeError("bad_args", "notes must be a non-empty list")
    specs = [_spec_from(item, index) for index, item in enumerate(notes)]
    end_time = max(s["start_time"] + s["duration"] for s in specs)
    obj, created = midi_clip_for_writing(ctx, track, slot, clip, as_bool(create, "create"),
                                         end_time)
    extended = extend_clip_to(obj, end_time, ctx.song) if as_bool(extend, "extend") else None
    ids, added, merged = _write_notes(obj, specs)
    result = _summary(ctx, obj)
    result.update({"added": added, "note_ids": ids})
    if merged:
        result["merged"] = merged
    if created:
        result["created"] = True
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    return result


@command("notes.replace", mutating=True,
         doc="Replace the notes of a clip (or of a time/pitch region) with new ones")
def notes_replace(ctx, track=None, slot=None, clip=None, notes=None, start=None, end=None,
                  pitch_min=None, pitch_max=None, create=False, extend=False):
    """Clear a region and write new notes in one undo step.

    Args:
        track, slot / clip: which clip.
        notes: same formats as notes.add ([] just clears).
        start, end: region (by note start, beats) to clear first; default the
            whole clip.
        pitch_min, pitch_max: limit the cleared region to a pitch range.
        create, extend: as in notes.add.

    Returns:
        {"clip", "name", "length", "removed": n, "added": n, "note_ids": [...],
         "merged"?: n (see notes.add)}
    """
    if not isinstance(notes, (list, tuple)):
        raise BridgeError("bad_args", "notes must be a list (use [] to only clear)")
    specs = [_spec_from(item, index) for index, item in enumerate(notes)]
    end_time = max([s["start_time"] + s["duration"] for s in specs] or [0.0])
    obj, created = midi_clip_for_writing(ctx, track, slot, clip, as_bool(create, "create"),
                                         end_time or None)
    removed = _remove(obj, _select(_all_notes(obj), start, end, None, pitch_min, pitch_max))
    extended = extend_clip_to(obj, end_time, ctx.song) \
        if as_bool(extend, "extend") and specs else None
    ids, added, merged = _write_notes(obj, specs)
    result = _summary(ctx, obj)
    result.update({"removed": removed, "added": added, "note_ids": ids})
    if merged:
        result["merged"] = merged
    if created:
        result["created"] = True
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    return result


@command("notes.clear", mutating=True, doc="Remove all notes, a region, or notes by id")
def notes_clear(ctx, track=None, slot=None, clip=None, start=None, end=None, pitch=None,
                pitch_min=None, pitch_max=None, note_ids=None, selected=False):
    """Remove notes.

    Args:
        track, slot / clip: which clip.
        start, end: note-start window in beats (omit both = whole clip).
        pitch / pitch_min / pitch_max: pitch filter (numbers or names).
        note_ids: only these notes (ids from notes.get).
        selected: only the notes selected in Live's clip editor.

    Returns:
        {"clip", "name", "length", "removed": n}
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    removed = _remove(obj, _select(_all_notes(obj), start, end, pitch, pitch_min, pitch_max,
                                   _selection_ids(obj, note_ids, selected)))
    result = _summary(ctx, obj)
    result["removed"] = removed
    return result


_MODIFIABLE = {"pitch", "start", "start_time", "duration", "velocity", "mute", "probability",
               "velocity_deviation", "release_velocity", "note_id"}


@command("notes.modify", mutating=True, doc="Change existing notes by note_id")
def notes_modify(ctx, track=None, slot=None, clip=None, changes=None):
    """Edit notes in place (keeps their ids and per-note expression).

    Args:
        track, slot / clip: which clip.
        changes: list of {"note_id": id, and any of "pitch" (number/name),
            "start", "duration", "velocity", "mute", "probability",
            "velocity_deviation", "release_velocity"}.

    Returns:
        {"clip", "name", "length", "modified": n, "merged"?: n}

    Gotchas:
        Unknown ids fail the whole command (not_found) — re-read with notes.get
        after deleting notes; ids of untouched notes never change. Live
        resolves same-pitch overlaps after the edit: a note moved onto the
        start of another note of that pitch deletes one of them, a note
        lengthened over the next one is cut at its start (``merged`` counts
        deleted notes).
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    if not isinstance(changes, (list, tuple)) or not changes:
        raise BridgeError("bad_args", "changes must be a non-empty list of {note_id, ...}")
    wanted = {}
    for index, change in enumerate(changes):
        if not isinstance(change, dict) or "note_id" not in change:
            raise BridgeError("bad_args", "changes[%d] must be an object with note_id" % index)
        unknown = set(change) - _MODIFIABLE
        if unknown:
            raise BridgeError("bad_args", "changes[%d]: unknown field(s) %s"
                              % (index, ", ".join(sorted(unknown))))
        wanted[as_int(change["note_id"], "changes[%d].note_id" % index)] = (index, change)
    vector = _all_notes(obj)
    by_id = dict((int(n.note_id), n) for n in vector)
    missing = sorted(set(wanted) - set(by_id))
    if missing:
        raise BridgeError("not_found", "no notes with id %s in this clip" %
                          ", ".join(str(m) for m in missing[:10]))
    for note_id, (index, change) in wanted.items():
        note = by_id[note_id]
        where = "changes[%d]" % index
        if "pitch" in change:
            note.pitch = note_to_midi(change["pitch"], where + ".pitch")
        start = change.get("start", change.get("start_time"))
        if start is not None:
            note.start_time = as_float(start, where + ".start", minimum=0.0)
        if "duration" in change:
            duration = as_float(change["duration"], where + ".duration")
            if duration <= 0:
                raise BridgeError("bad_args", "%s.duration must be > 0" % where)
            note.duration = duration
        if "velocity" in change:
            note.velocity = max(1.0, as_float(change["velocity"], where + ".velocity", 0, 127))
        if "mute" in change:
            note.mute = as_bool(change["mute"], where + ".mute")
        if "probability" in change:
            note.probability = as_float(change["probability"], where + ".probability", 0, 1)
        if "velocity_deviation" in change:
            note.velocity_deviation = as_float(change["velocity_deviation"],
                                               where + ".velocity_deviation", -127, 127)
        if "release_velocity" in change:
            note.release_velocity = as_float(change["release_velocity"],
                                             where + ".release_velocity", 0, 127)
    before = len(vector)
    _apply(obj, vector)
    result = _summary(ctx, obj)
    result["modified"] = len(wanted)
    after = _note_count(obj)
    if after is not None and after < before:
        result["merged"] = before - after
    return result


# ==========================================================================
# commands: transform
# ==========================================================================

def _quantize(notes, grid, strength, swing, quantize_ends, offset=0.0):
    changed = 0
    for note in notes:
        index = int(math.floor((note.start_time - offset) / grid + 0.5))
        target = offset + index * grid
        if swing and index % 2 == 1:
            target += swing * grid
        new_start = note.start_time + (target - note.start_time) * strength
        if quantize_ends:
            end = note.start_time + note.duration
            end_target = offset + math.floor((end - offset) / grid + 0.5) * grid
            new_end = end + (end_target - end) * strength
            if new_end - new_start < grid * 0.25:
                new_end = new_start + max(grid * 0.25, 1e-3)
            note.duration = new_end - new_start
        if abs(new_start - note.start_time) > 1e-9 or quantize_ends:
            changed += 1
        note.start_time = max(0.0, new_start)
    return changed


def _humanize(notes, timing, velocity, rng):
    for note in notes:
        if timing:
            note.start_time = max(0.0, note.start_time + rng.uniform(-timing, timing))
        if velocity:
            note.velocity = min(127.0, max(1.0, note.velocity + rng.uniform(-velocity, velocity)))
    return len(notes)


def _legato(notes, gap, clip_end):
    starts = sorted(set(round(n.start_time, 6) for n in notes))
    changed = 0
    for note in notes:
        position = bisect.bisect_right(starts, round(note.start_time, 6) + 1e-9)
        end = starts[position] if position < len(starts) else clip_end
        length = end - note.start_time - gap
        if length > 1e-4:
            note.duration = length
            changed += 1
    return changed


def _fix_overlaps(notes, gap):
    changed = 0
    by_pitch = {}
    for note in notes:
        by_pitch.setdefault(note.pitch, []).append(note)
    for group in by_pitch.values():
        group.sort(key=lambda n: n.start_time)
        for current, following in zip(group, group[1:]):
            limit = following.start_time - current.start_time - gap
            if current.start_time + current.duration > following.start_time - gap + 1e-9 \
                    and limit > 1e-4:
                current.duration = limit
                changed += 1
    return changed


def _retrograde(notes):
    """Mirror the notes in time inside the span they cover (last note first)."""
    if not notes:
        return 0
    begin = min(n.start_time for n in notes)
    finish = max(n.start_time + n.duration for n in notes)
    for note in notes:
        note.start_time = max(0.0, begin + finish - (note.start_time + note.duration))
    return len(notes)


def _check_pitches(notes, target, what):
    bad = [(n, target(n)) for n in notes if not 0 <= target(n) <= 127]
    if bad:
        raise BridgeError("invalid_state", "%s would push %d note(s) out of 0..127 (e.g. "
                          "pitch %d -> %d); nothing was changed"
                          % (what, len(bad), bad[0][0].pitch, bad[0][1]))


@command("notes.transform", mutating=True,
         doc="Shift / quantize / humanize / retrograde / transpose (chromatic or in key) / "
             "invert / fit to scale / velocity / legato on selected notes")
def notes_transform(ctx, track=None, slot=None, clip=None, start=None, end=None, pitch=None,
                    pitch_min=None, pitch_max=None, note_ids=None, quantize=None,
                    strength=1.0, swing=0.0, quantize_ends=False, humanize_timing=0.0,
                    humanize_velocity=0.0, seed=None, transpose=0, velocity_scale=None,
                    velocity_offset=0.0, velocity_set=None, velocity_min=1, velocity_max=127,
                    legato=False, fix_overlaps=False, gap=0.0, shift=0.0, retrograde=False,
                    transpose_steps=0, invert=None, fit_scale=None, key=None,
                    selected=False):
    """Edit many notes at once. Operations run in this order on the selected
    notes: shift -> quantize -> humanize -> retrograde -> transpose ->
    transpose_steps -> invert -> fit_scale -> velocity -> legato/fix_overlaps.

    Args:
        track, slot / clip: which clip.
        Selection (all optional, default every note): start/end (note-start
            window, beats), pitch (one or a list) or pitch_min/pitch_max,
            note_ids, selected (the notes selected in Live's clip editor).
        shift: move the notes by this many beats (negative = earlier).
        quantize: grid — beats (0.25) or "1/16", "1/8T", "1/4." ...
        strength: 0..1 quantize strength (1 = on the grid).
        swing: 0..1 — delays every second grid step by that fraction of a step
            (0.33 ~ triplet shuffle).
        quantize_ends: also snap note ends.
        humanize_timing: max random shift in beats (e.g. 0.02).
        humanize_velocity: max random velocity change (e.g. 8).
        seed: int for reproducible humanizing (the seed used is returned).
        retrograde: play the selection backwards (mirrored in the span it covers).
        transpose: semitones (+12 = octave up).
        transpose_steps: scale degrees in ``key`` (+2 = up a third in key;
            notes outside the scale keep their offset from the degree below).
        invert: mirror pitches around an axis — true (the first note's pitch)
            or a pitch (60 / "C3"); combine with fit_scale to stay in key.
        fit_scale: snap every pitch into ``key``: true / "nearest", "up" or "down".
        key: the scale for transpose_steps / fit_scale — "song" (default: the
            song's root + scale), "A minor", "F# dorian", {root, scale}.
        velocity_scale: multiply velocities (0.8); velocity_offset: add;
            velocity_set: set all to one value; then clamp to
            [velocity_min, velocity_max].
        legato: stretch each note to the next note start (monophonic line;
            the last note to the clip end), minus ``gap`` beats.
        fix_overlaps: shorten notes that overlap the next note of the same
            pitch (keeps ``gap`` beats).

    Returns:
        {"clip", "name", "length", "selected": n, "operations": [...], "seed"?,
         "key"?, "merged"?: n}

    Gotchas:
        Live keeps no overlapping notes of the same pitch: when quantize or
        humanize puts two same-pitch notes on one start, Live deletes one
        (``merged`` counts them), and overlaps are cut at the next note.
        Transposing notes out of 0..127 (or shifting before beat 0) fails the
        whole command. Quantize here works on any grid; clips.quantize is
        Live's own (fixed grids, whole clip, uses the song swing). To copy
        notes elsewhere use notes.duplicate.
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    vector = _all_notes(obj)
    chosen = _select(vector, start, end, pitch, pitch_min, pitch_max,
                     _selection_ids(obj, note_ids, selected))
    operations = []
    result = _summary(ctx, obj)
    strength = as_float(strength, "strength", 0.0, 1.0)
    swing = as_float(swing, "swing", 0.0, 1.0)
    transpose = as_int(transpose, "transpose", -127, 127)
    transpose_steps = as_int(transpose_steps, "transpose_steps", -70, 70)
    shift = as_float(shift, "shift", -100000.0, 100000.0)
    humanize_timing = as_float(humanize_timing, "humanize_timing", 0.0, 4.0)
    humanize_velocity = as_float(humanize_velocity, "humanize_velocity", 0.0, 127.0)
    velocity_min = as_float(velocity_min, "velocity_min", 1.0, 127.0)
    velocity_max = as_float(velocity_max, "velocity_max", 1.0, 127.0)
    gap = as_float(gap, "gap", 0.0, 16.0)
    if velocity_min > velocity_max:
        raise BridgeError("bad_args", "velocity_min must not exceed velocity_max")
    fit = None
    if fit_scale is not None and fit_scale is not False:
        fit = "nearest" if fit_scale is True else str(fit_scale).strip().lower()
        if fit not in ("nearest", "up", "down"):
            raise BridgeError("bad_args", "fit_scale must be true, \"nearest\", \"up\" or "
                              "\"down\"")
    axis = None
    if invert is not None and invert is not False:
        if invert is True:
            axis = "first"
        else:
            axis = note_to_midi(invert, "invert")
    scale_key = parse_key(ctx, key) if (transpose_steps or fit) else None
    if shift:
        early = [n for n in chosen if n.start_time + shift < -1e-9]
        if early:
            raise BridgeError("invalid_state", "shifting by %s would move %d note(s) before "
                              "the clip start (beat 0); nothing was changed"
                              % (rnd(shift), len(early)))
        for note in chosen:
            note.start_time = max(0.0, note.start_time + shift)
        operations.append("shift")
    if quantize is not None:
        grid = parse_grid(quantize, "quantize")
        _quantize(chosen, grid, strength, swing, as_bool(quantize_ends, "quantize_ends"))
        operations.append("quantize")
    if humanize_timing or humanize_velocity:
        if seed is None:
            seed = random.randrange(1, 1 << 30)
        seed = as_int(seed, "seed")
        _humanize(chosen, humanize_timing, humanize_velocity, random.Random(seed))
        operations.append("humanize")
        result["seed"] = seed
    if as_bool(retrograde, "retrograde"):
        _retrograde(chosen)
        operations.append("retrograde")
    if transpose:
        _check_pitches(chosen, lambda n: n.pitch + transpose, "transposing by %d" % transpose)
        for note in chosen:
            note.pitch = int(note.pitch + transpose)
        operations.append("transpose")
    if transpose_steps:
        _check_pitches(chosen, lambda n: scale_key.step(n.pitch, transpose_steps),
                       "transposing by %d scale steps" % transpose_steps)
        for note in chosen:
            note.pitch = int(scale_key.step(note.pitch, transpose_steps))
        operations.append("transpose_steps")
    if axis is not None:
        if chosen:
            pivot = min(chosen, key=lambda n: (n.start_time, n.pitch)).pitch \
                if axis == "first" else axis
            _check_pitches(chosen, lambda n: 2 * pivot - n.pitch, "inverting")
            for note in chosen:
                note.pitch = int(2 * pivot - note.pitch)
            result["axis"] = int(pivot)
        operations.append("invert")
    if fit:
        _check_pitches(chosen, lambda n: scale_key.snap(n.pitch, fit), "fitting to the scale")
        moved = 0
        for note in chosen:
            target = int(scale_key.snap(note.pitch, fit))
            if target != note.pitch:
                moved += 1
                note.pitch = target
        operations.append("fit_scale")
        result["fitted"] = moved
    if scale_key is not None:
        result["key"] = scale_key.label()
    velocity_changes = velocity_scale is not None or velocity_set is not None or \
        velocity_offset not in (0, 0.0, None) or velocity_min > 1 or velocity_max < 127
    if velocity_changes:
        scale = as_float(velocity_scale, "velocity_scale", 0.0, 10.0, allow_none=True)
        fixed = as_float(velocity_set, "velocity_set", 1.0, 127.0, allow_none=True)
        offset = as_float(velocity_offset or 0.0, "velocity_offset", -127.0, 127.0)
        for note in chosen:
            value = fixed if fixed is not None else note.velocity
            if scale is not None:
                value *= scale
            value += offset
            note.velocity = min(velocity_max, max(velocity_min, value))
        operations.append("velocity")
    if as_bool(legato, "legato"):
        _legato(chosen, gap, _clip_end(obj))
        operations.append("legato")
    if as_bool(fix_overlaps, "fix_overlaps"):
        _fix_overlaps(chosen, gap)
        operations.append("fix_overlaps")
    if not operations:
        raise BridgeError("bad_args", "nothing to do — pass shift, quantize, humanize_*, "
                          "retrograde, transpose, transpose_steps, invert, fit_scale, "
                          "velocity_*, legato or fix_overlaps")
    if chosen:
        before = len(vector)
        _apply(obj, vector)
        after = _note_count(obj)
        if after is not None and after < before:
            result["merged"] = before - after
    result.update({"selected": len(chosen), "operations": operations})
    return result


@command("notes.duplicate", mutating=True,
         doc="Copy a region of notes to other times in the same clip (repeat a phrase, "
             "optionally transposed or moved in key)")
def notes_duplicate(ctx, track=None, slot=None, clip=None, start=None, end=None,
                    destination=None, times=1, pitch=None, pitch_min=None, pitch_max=None,
                    note_ids=None, selected=False, transpose=0, transpose_steps=0, key=None,
                    extend=True):
    """Duplicate notes inside a clip ("copy bar 1 to bars 2-4", "repeat this phrase a
    fifth up").

    Args:
        track, slot / clip: which clip.
        start, end: the region (note starts, beats; default the clip loop).
        destination: where the first copy starts (beats; default right after
            the region). Copy k lands at destination + k * (end - start).
        times: number of copies (1..64).
        pitch / pitch_min / pitch_max, note_ids, selected: copy only these
            notes (``selected`` = Live's clip-editor selection).
        transpose: semitones for the copies; transpose_steps: scale degrees
            in ``key`` ("song" default, "A minor", ...).
        extend: grow the clip loop to contain the copies (default true).

    Returns:
        {"clip", "name", "length", "copied": notes per copy, "copies", "added",
         "at": [copy starts], "note_ids"?, "extended_to"?, "merged"?}

    Gotchas:
        Chromatic copies use Live's ``duplicate_notes_by_id`` (per-note
        expression travels along); scale-step copies are written as new notes.
        Copies replace same-pitch notes they overlap (Live never keeps two
        overlapping notes of one pitch — ``merged``).
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    loop_start, loop_end = clip_region(obj)
    begin = loop_start if start is None else as_float(start, "start", minimum=0.0)
    finish = loop_end if end is None else as_float(end, "end", minimum=0.0)
    if finish <= begin:
        raise BridgeError("bad_args", "end must be after start")
    span = finish - begin
    times = as_int(times, "times", 1, 64)
    transpose = as_int(transpose, "transpose", -127, 127)
    transpose_steps = as_int(transpose_steps, "transpose_steps", -70, 70)
    target = finish if destination is None else as_float(destination, "destination",
                                                         minimum=0.0)
    notes = _select(_all_notes(obj), begin, finish, pitch, pitch_min, pitch_max,
                    _selection_ids(obj, note_ids, selected))
    if not notes:
        raise BridgeError("not_found", "no notes start in %s..%s with these filters"
                          % (rnd(begin), rnd(finish)))
    scale_key = parse_key(ctx, key) if transpose_steps else None

    def moved(note):
        value = note.pitch + transpose
        if scale_key is not None:
            value = scale_key.step(value, transpose_steps)
        return value

    _check_pitches(notes, moved, "transposing the copies")
    starts = [target + k * span for k in range(times)]
    last_end = max(starts[-1] + (n.start_time - begin) + n.duration for n in notes)
    extended = extend_clip_to(obj, last_end, ctx.song) if as_bool(extend, "extend") else None
    first = min(n.start_time for n in notes)
    before = _note_count(obj)
    ids = []
    if scale_key is None and compat.has(obj, "duplicate_notes_by_id"):
        source_ids = [int(n.note_id) for n in notes]
        for copy_start in starts:
            new_ids = live_call("duplicate_notes_by_id", obj.duplicate_notes_by_id,
                                source_ids, float(copy_start + (first - begin)), transpose)
            try:
                ids.extend(int(i) for i in new_ids)
            except TypeError:
                pass
    else:
        specs = []
        for copy_start in starts:
            for note in notes:
                specs.append({"pitch": int(moved(note)),
                              "start_time": copy_start + (note.start_time - begin),
                              "duration": float(note.duration),
                              "velocity": float(note.velocity), "mute": bool(note.mute),
                              "probability": float(compat.safe_getattr(note, "probability",
                                                                       1.0)),
                              "velocity_deviation": float(compat.safe_getattr(
                                  note, "velocity_deviation", 0.0)),
                              "release_velocity": float(compat.safe_getattr(
                                  note, "release_velocity", 64.0))})
        ids = _add_specs(obj, specs)
    after = _note_count(obj)
    result = _summary(ctx, obj)
    added = len(notes) * times
    result.update({"copied": len(notes), "copies": times, "at": [rnd(t) for t in starts]})
    if before is not None and after is not None:
        merged = max(0, before + added - after)
        result["added"] = after - before
        if merged:
            result["merged"] = merged
    else:
        result["added"] = added
    if ids:
        result["note_ids"] = ids[:512]
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    if scale_key is not None:
        result["key"] = scale_key.label()
    return result


@command("notes.select", doc="Select notes in Live's clip editor (by filter / ids / all / "
                             "none) — or read the current selection")
def notes_select(ctx, track=None, slot=None, clip=None, start=None, end=None, pitch=None,
                 pitch_min=None, pitch_max=None, note_ids=None, all=False, none=False,
                 add=False):
    """Highlight notes for the user, or find out which notes they selected.

    Args:
        track, slot / clip: which clip ("selected" = the clip in Live's detail view).
        start/end, pitch / pitch_min / pitch_max, note_ids: the notes to select.
        all: select every note; none: deselect everything.
        add: add to the current selection instead of replacing it.
        (no filter at all): only read the selection.

    Returns:
        {"clip", "name", "length", "selected": n, "note_ids": [...]} — the
        selection after the command.

    Gotchas:
        Live shows the selection when the clip is open in the Detail view
        (view.select / live_view_show). notes.get / notes.transform /
        notes.clear / notes.duplicate accept ``selected=true``.
    """
    obj = resolve_clip(ctx, track, slot, clip, need="midi")
    filters = (start, end, pitch, pitch_min, pitch_max, note_ids)
    if as_bool(all, "all") and as_bool(none, "none"):
        raise BridgeError("bad_args", "pass all or none, not both")
    if all or none or any(f is not None for f in filters):
        if not compat.has(obj, "select_notes_by_id"):
            raise BridgeError("unsupported", "this Live version cannot select notes")
        if none or (not as_bool(add, "add")):
            live_call("deselect_all_notes", obj.deselect_all_notes)
        if all:
            live_call("select_all_notes", obj.select_all_notes)
        elif not none:
            chosen = _select(_all_notes(obj), start, end, pitch, pitch_min, pitch_max,
                             note_ids)
            if chosen:
                live_call("select_notes_by_id", obj.select_notes_by_id,
                          [int(n.note_id) for n in chosen])
    ids = _selected_ids(obj)
    result = _summary(ctx, obj)
    result.update({"selected": len(ids), "note_ids": ids[:2000]})
    return result


# ==========================================================================
# commands: composers
# ==========================================================================

def _pattern_rows(pattern):
    """``[(key, pitch or None, steps)]`` — ``pitch`` is None for a row named after a pad
    that is not a note or GM drum name (resolved against the kit later)."""
    if isinstance(pattern, dict):
        items = list(pattern.items())
    elif isinstance(pattern, (list, tuple)):
        items = []
        for index, entry in enumerate(pattern):
            if not isinstance(entry, dict) or "pitch" not in entry or "steps" not in entry:
                raise BridgeError("bad_args", "pattern[%d] must be {pitch, steps}" % index)
            items.append((entry["pitch"], entry["steps"]))
    else:
        raise BridgeError("bad_args", "pattern must be an object like {\"C1\": \"x...x...\"}")
    if not items:
        raise BridgeError("bad_args", "pattern is empty")
    rows = []
    for key, steps in items:
        try:
            pitch = note_to_midi(key, "pattern key %r" % (key,))
        except BridgeError:
            if not isinstance(key, str) or not key.strip() or key.strip().lstrip("-").isdigit():
                raise
            pitch = None
        if not isinstance(steps, str):
            raise BridgeError("bad_args", "pattern[%r] must be a string of steps" % (key,))
        cleaned = re.sub(r"[\s|]+", "", steps)
        bad = sorted(set(re.sub(r"[xXoO_=.\-0-9]", "", cleaned)))
        if bad:
            raise BridgeError("bad_args", "pattern[%r]: unknown step character(s) %s — use x "
                              "(hit), X (accent), o (ghost), 1-9 (velocity level), _ (hold), "
                              ". or - (rest)" % (key, " ".join(repr(c) for c in bad)))
        rows.append((key, pitch, cleaned))
    return rows


#: GM drum names -> the instrument family used to match Drum Rack pad names.
_DRUM_GROUPS = {
    "kick": "kick", "bd": "kick", "bassdrum": "kick", "kick2": "kick",
    "snare": "snare", "sd": "snare", "snare2": "snare",
    "clap": "clap", "handclap": "clap",
    "hat": "hat", "hh": "hat", "hihat": "hat", "chh": "hat", "closedhat": "hat",
    "closedhihat": "hat", "pedalhat": "hat",
    "ohh": "ohh", "openhat": "ohh", "openhihat": "ohh",
    "rim": "rim", "rimshot": "rim", "sidestick": "rim",
    "crash": "crash", "crash2": "crash", "splash": "crash", "china": "crash",
    "ride": "ride", "ridebell": "ride",
    "lowtom": "lowtom", "tomlow": "lowtom", "floortom": "lowtom",
    "midtom": "midtom", "tommid": "midtom", "hightom": "hightom", "tomhigh": "hightom",
    "cowbell": "cowbell", "shaker": "shaker", "maracas": "shaker",
    "tambourine": "tambourine", "clave": "clave", "woodblock": "woodblock",
}
_OPEN_RE = re.compile(r"open|\bohh?\b")
_GROUP_TESTS = {
    "kick": lambda n: re.search(r"kick|\bkik\b|\bkck\b|\bbd\b|bass ?drum", n),
    "snare": lambda n: re.search(r"snare|\bsnr\b|\bsd\b", n),
    "clap": lambda n: re.search(r"clap|\bclp\b", n),
    "hat": lambda n: re.search(r"hat|\bhh\b|\bchh\b|hi ?hat", n) and not _OPEN_RE.search(n),
    "ohh": lambda n: re.search(r"hat|\bhh\b|\bohh?\b", n) and _OPEN_RE.search(n),
    "rim": lambda n: re.search(r"\brim|stick", n),
    "crash": lambda n: re.search(r"crash|splash|china", n),
    "ride": lambda n: re.search(r"\bride", n),
    "lowtom": lambda n: re.search(r"tom", n) and re.search(r"low|\blo\b|floor", n),
    "midtom": lambda n: re.search(r"tom", n) and re.search(r"mid", n),
    "hightom": lambda n: re.search(r"tom", n) and re.search(r"high|\bhi\b", n),
    "cowbell": lambda n: re.search(r"cow ?bell", n),
    "shaker": lambda n: re.search(r"shake|maraca", n),
    "tambourine": lambda n: re.search(r"tamb", n),
    "clave": lambda n: re.search(r"clave", n),
    "woodblock": lambda n: re.search(r"wood|block", n),
}


def _normal_name(text):
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", str(text or "").lower())).strip()


def find_drum_rack(track_obj):
    """The first Drum Rack on a track (top level, or one level into an instrument rack)."""
    get = compat.safe_getattr
    for device in get(track_obj, "devices", ()) or ():
        if get(device, "can_have_drum_pads", False):
            return device
    for device in get(track_obj, "devices", ()) or ():
        if not get(device, "can_have_chains", False):
            continue
        for chain in get(device, "chains", ()) or ():
            for inner in get(chain, "devices", ()) or ():
                if get(inner, "can_have_drum_pads", False):
                    return inner
    return None


def kit_pads(rack):
    """``{note: pad name}`` of the pads that hold something."""
    get = compat.safe_getattr
    pads = {}
    for pad in get(rack, "drum_pads", ()) or ():
        chains = get(pad, "chains", ()) or ()
        try:
            filled = len(chains) > 0
        except TypeError:
            filled = False
        if filled:
            pads[int(get(pad, "note", 0))] = str(get(pad, "name", ""))
    return pads


def map_rows_to_kit(rows, pads, rack_name):
    """Resolve pattern rows against a Drum Rack's pad names.

    Returns ``(rows with pitches, mapping, warnings)``: a GM drum name ("hat",
    "clap", ...) goes to a filled pad whose name matches its family (the GM
    pad first), an unknown row name to the pad with that name; explicit note
    names / numbers are kept.  ``warnings`` lists rows that hit an empty pad.
    """
    resolved, mapping, warnings = [], {}, []
    names = dict((note, _normal_name(name)) for note, name in pads.items())
    for key, pitch, steps in rows:
        text = re.sub(r"[\s_\-]+", "", str(key).lower()) if isinstance(key, str) else None
        group = _DRUM_GROUPS.get(text) if text else None
        chosen, how = pitch, "note"
        if group is not None and pads:
            test = _GROUP_TESTS[group]
            gm = DRUM_NOTES[text]
            if gm in names and test(names[gm]):
                chosen, how = gm, "pad name"
            else:
                hits = [note for note in sorted(names) if test(names[note])]
                if hits:
                    chosen, how = hits[0], "pad name"
                else:
                    chosen, how = gm, "general midi"
        elif pitch is None:
            wanted = _normal_name(key)
            hits = []
            for check in (lambda n: n == wanted, lambda n: n.startswith(wanted),
                          lambda n: wanted in n):
                hits = [note for note in sorted(names) if check(names[note])]
                if hits:
                    break
            if len(hits) != 1:
                available = ", ".join("%d %s" % (n, pads[n]) for n in sorted(pads)[:24])
                reason = "matches several pads" if hits else "matches no pad"
                raise BridgeError("bad_args", "pattern row %r is not a note or drum name and "
                                  "%s of %r (filled pads: %s)"
                                  % (key, reason, rack_name, available or "none"))
            chosen, how = hits[0], "pad name"
        elif group is not None:
            how = "general midi"
        resolved.append((key, chosen, steps))
        entry = {"note": chosen, "how": how}
        if chosen in pads:
            entry["pad"] = pads[chosen]
        else:
            entry["pad"] = None
            warnings.append("row %r plays note %d (%s) but that pad of %r is empty"
                            % (key, chosen, midi_to_note(chosen), rack_name))
        mapping[str(key)] = entry
    return resolved, mapping, warnings


@command("notes.write_pattern", mutating=True,
         doc="Write a step-sequencer pattern: {pitch: 'x...x...'} per row")
def notes_write_pattern(ctx, track=None, slot=None, clip=None, pattern=None, step=0.25,
                        start=0.0, velocity=100, accent_velocity=127, ghost_velocity=50,
                        gate=0.9, repeat=1, swing=0.0, clear=True, create=True, extend=True,
                        kit=True):
    """Write drum/step patterns.

    Args:
        track, slot / clip: which clip (created when missing — see ``create``).
        pattern: {row: steps}. Row = pitch number, note name ("C1" = 36 = kick
            pad) or drum name ("kick", "snare", "clap", "hat", "ohh", "ride",
            "crash", "rim", "tom_low"...). Steps, one char per step:
            ``x`` hit, ``X`` accent, ``o`` ghost, ``1``-``9`` velocity level
            (9 = 127), ``_`` or ``=`` hold the previous hit one more step,
            ``.`` ``-`` ``0`` rest; spaces and ``|`` are ignored.
        step: step length — beats or "1/16" (default 0.25 = 16th notes).
        start: where the pattern begins, beats.
        velocity, accent_velocity, ghost_velocity: velocities for x / X / o.
        gate: note length as a fraction of a step (0.05..1).
        repeat: write the pattern this many times back to back.
        swing: 0..1 fraction of a step to delay every second step.
        clear: first remove existing notes of those pitches in the written range.
        create: create a MIDI clip when the slot is empty (length = pattern,
            whole bars); with no slot the first empty slot is used.
        extend: grow the clip loop when the pattern is longer than it.
        kit: match drum names against the track's Drum Rack (default true):
            "hat" goes to the pad whose name says closed hat (e.g. "HH Closed
            808" on D#1) instead of the fixed General-MIDI note, and a row may
            be named after a pad ("Perc 2"). Note names and numbers are kept.

    Returns:
        {"clip", "name", "length", "added": n, "removed": n, "rows": {row: pitch},
         "pattern_length": beats, "kit"?: {"device", "rows": {row: {"note",
         "pad", "how"}}}, "warnings"?: [rows that hit empty pads],
         "created"?, "extended_to"?, "merged"?}

    Example:
        pattern={"kick": "x...x...x...x...", "snare": "....X.......X...",
                 "hat": "x.x.x.x.x.x.x.xo"}
    """
    rows = _pattern_rows(pattern)
    step = parse_grid(step, "step")
    start = as_float(start, "start", minimum=0.0)
    velocity = as_float(velocity, "velocity", 1, 127)
    accent_velocity = as_float(accent_velocity, "accent_velocity", 1, 127)
    ghost_velocity = as_float(ghost_velocity, "ghost_velocity", 1, 127)
    gate = as_float(gate, "gate", 0.05, 1.0)
    repeat = as_int(repeat, "repeat", 1, 256)
    swing = as_float(swing, "swing", 0.0, 1.0)
    steps_per_row = max(len(steps) for _key, _pitch, steps in rows)
    if steps_per_row == 0:
        raise BridgeError("bad_args", "pattern has no steps")
    pattern_length = steps_per_row * step
    total = pattern_length * repeat
    kit = as_bool(kit, "kit")
    if not kit and any(p is None for _k, p, _s in rows):
        raise BridgeError("bad_args", "pattern row %r is not a note or drum name (pad names "
                          "need kit=true)" % [k for k, p, _s in rows if p is None][0])
    obj, created = midi_clip_for_writing(ctx, track, slot, clip, as_bool(create, "create"),
                                         start + total)
    kit_info, warnings = None, []
    rack = find_drum_rack(owner_track(obj)) if kit else None
    if rack is not None:
        rows, mapping, warnings = map_rows_to_kit(rows, kit_pads(rack),
                                                  compat.safe_getattr(rack, "name", ""))
        kit_info = {"device": compat.safe_getattr(rack, "name"), "rows": mapping}
    elif any(p is None for _k, p, _s in rows):
        raise BridgeError("bad_args", "pattern row %r is not a note or drum name and the "
                          "track has no Drum Rack to look it up in"
                          % [k for k, p, _s in rows if p is None][0])
    specs = []
    for _key, pitch, steps in rows:
        for rep in range(repeat):
            offset = start + rep * pattern_length
            index = 0
            while index < len(steps):
                char = steps[index]
                if char in "xXoO123456789":
                    held = 0
                    while index + 1 + held < len(steps) and steps[index + 1 + held] in "_=":
                        held += 1
                    if char == "x":
                        vel = velocity
                    elif char == "X":
                        vel = accent_velocity
                    elif char in "oO":
                        vel = ghost_velocity
                    else:
                        vel = round(127.0 * int(char) / 9.0)
                    time = offset + index * step + (swing * step if index % 2 == 1 else 0.0)
                    specs.append({"pitch": pitch, "start_time": time,
                                  "duration": held * step + step * gate,
                                  "velocity": max(1.0, float(vel)), "mute": False,
                                  "probability": 1.0, "velocity_deviation": 0.0,
                                  "release_velocity": 64.0})
                    index += 1 + held
                else:
                    index += 1
    removed = 0
    if as_bool(clear, "clear") and not created:
        pitches = sorted(set(p for _k, p, _s in rows))
        removed = _remove(obj, _select(_all_notes(obj), start, start + total, pitches))
    extended = extend_clip_to(obj, start + total, ctx.song) \
        if as_bool(extend, "extend") else None
    ids, added, merged = _write_notes(obj, specs)
    result = _summary(ctx, obj)
    if merged:
        result["merged"] = merged
    result.update({"added": added, "removed": removed,
                   "rows": dict((str(k), p) for k, p, _s in rows),
                   "pattern_length": rnd(pattern_length)})
    if kit_info is not None:
        result["kit"] = kit_info
    if warnings:
        result["warnings"] = warnings
    if created:
        result["created"] = True
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    return result


_VOICINGS = ("close", "open", "drop2", "drop3", "spread")


def _chord_items(chords):
    if isinstance(chords, str):
        chords = [c for c in re.split(r"[\s|,]+", chords.strip()) if c]
    if not isinstance(chords, (list, tuple)) or not chords:
        raise BridgeError("bad_args", "chords must be a non-empty list like "
                          "[\"Am7\", \"F\", \"C\", \"G/B\"]")
    items = []
    for index, entry in enumerate(chords):
        if isinstance(entry, str):
            items.append({"chord": entry})
        elif isinstance(entry, dict) and "chord" in entry:
            unknown = set(entry) - {"chord", "start", "duration", "velocity", "octave",
                                    "inversion"}
            if unknown:
                raise BridgeError("bad_args", "chords[%d]: unknown field(s) %s"
                                  % (index, ", ".join(sorted(unknown))))
            items.append(dict(entry))
        else:
            raise BridgeError("bad_args", "chords[%d] must be a symbol or {chord, start?, "
                              "duration?, velocity?, octave?, inversion?}" % index)
    return items


@command("notes.write_chords", mutating=True,
         doc="Write a chord progression from symbols like Am7, F, G/B")
def notes_write_chords(ctx, track=None, slot=None, clip=None, chords=None, start=0.0,
                       duration=None, octave=3, velocity=90, voicing="close", inversion=0,
                       voice_leading=False, bass=False, strum=0.0, clear=True, create=True,
                       extend=True, key=None):
    """Write chords.

    Args:
        track, slot / clip: which clip (created when missing — see ``create``).
        chords: list of symbols ["Am7", "F", "C", "G/B", "N.C."] or objects
            {"chord": "Dm9", "start": beats?, "duration": beats?, "velocity"?,
            "octave"?, "inversion"?}; a single string "Am7 F C G" works too.
            Chords without ``start`` follow each other. Also Roman numerals in
            ``key`` ("I V vi IV", "ii7 V7 Imaj7", "i bVI bIII bVII", "vii°",
            "iiø7"; case = major/minor) and scale degrees ("1 5 6 4"; "57" =
            the diatonic seventh chord on degree 5).
        key: key for numerals/degrees — "song" (default: the song's root and
            scale), "A minor", "F# dorian", "Bb", "Am" or {root, scale}.
        start: where the first chord begins, beats.
        duration: default chord length in beats (default one bar).
        octave: octave of the chord root (C3 = 60; default 3).
        velocity: 1..127.
        voicing: "close" (default), "open", "drop2", "drop3", "spread".
        inversion: 0 = root position, 1 = first inversion ...
        voice_leading: pick the inversion of each chord that moves least from
            the previous one (overrides ``inversion`` after the first chord).
        bass: add the root (or the slash bass) an octave below the chord.
        strum: delay between successive chord notes, beats (0.02 = light strum).
        clear: first remove existing notes in the written time range.
        create, extend: as in notes.write_pattern.

    Returns:
        {"clip", "name", "length", "added": n, "removed": n,
         "chords": [{"chord", "start", "duration", "pitches": [...], "notes": ["A3", ...]}],
         "key"? (when numerals/degrees were used), "created"?, "extended_to"?, "merged"?}

    Gotchas:
        Supported: triads, sus2/4, 5, 6, 6/9, 7, maj7, m7, m7b5 (ø), dim7, mMaj7,
        aug7, 9/maj9/m9, 11, 13, add9/add11, alterations b5 #5 b9 #9 #11 b13,
        no3/no5 and slash chords. Slash bass notes go below the chord. Note
        names are spelled with flats for flat roots / flat keys ("Bb3").
    """
    if voicing not in _VOICINGS:
        raise BridgeError("bad_args", "voicing must be one of %s" % ", ".join(_VOICINGS))
    default_duration = as_float(duration, "duration", allow_none=True) \
        if duration is not None else beats_per_bar(ctx.song)
    if default_duration <= 0:
        raise BridgeError("bad_args", "duration must be > 0")
    items = _chord_items(chords)
    cursor = as_float(start, "start", minimum=0.0)
    octave = as_int(octave, "octave", -1, 8)
    velocity = as_float(velocity, "velocity", 1, 127)
    inversion = as_int(inversion, "inversion", 0, 12)
    strum = as_float(strum, "strum", 0.0, 4.0)
    voice_leading = as_bool(voice_leading, "voice_leading")
    bass = as_bool(bass, "bass")
    specs, written, previous = [], [], None
    first_start = cursor
    key_cache = []

    def key_getter():
        if not key_cache:
            key_cache.append(parse_key(ctx, key))
        return key_cache[0]

    for index, item in enumerate(items):
        where = "chords[%d]" % index
        chord_start = as_float(item["start"], where + ".start", minimum=0.0) \
            if item.get("start") is not None else cursor
        chord_duration = as_float(item["duration"], where + ".duration") \
            if item.get("duration") is not None else default_duration
        if chord_duration <= 0:
            raise BridgeError("bad_args", "%s.duration must be > 0" % where)
        cursor = chord_start + chord_duration
        first_start = min(first_start, chord_start)
        parsed = chord_from_symbol(item["chord"], key_getter)
        if parsed is None:
            written.append({"chord": item["chord"], "start": rnd(chord_start),
                            "duration": rnd(chord_duration), "pitches": []})
            continue
        root, intervals, slash, flats = parsed
        chord_octave = as_int(item.get("octave", octave), where + ".octave", -1, 8)
        chord_inversion = as_int(item.get("inversion", inversion), where + ".inversion", 0, 12)
        if voice_leading and previous and "inversion" not in item:
            pitches = _closest_voicing(root, intervals, chord_octave, previous)
            if voicing != "close":
                pitches = revoice(pitches, voicing)
        else:
            pitches = voice_chord(root, intervals, chord_octave, chord_inversion, voicing)
        previous = list(pitches)
        bass_pc = slash if slash is not None else (root if bass else None)
        if bass_pc is not None:
            low = pitches[0] - 1
            bass_note = low - ((low - bass_pc) % 12)
            if bass_note >= 0:
                pitches = [bass_note] + pitches
        if pitches[0] < 0 or pitches[-1] > 127:
            raise BridgeError("bad_args", "%s %r at octave %d leaves the MIDI range — lower "
                              "or raise octave" % (where, item["chord"], chord_octave))
        chord_velocity = as_float(item.get("velocity", velocity), where + ".velocity", 1, 127)
        for order, pitch in enumerate(pitches):
            offset = min(order * strum, chord_duration * 0.5)
            specs.append({"pitch": pitch, "start_time": chord_start + offset,
                          "duration": max(chord_duration - offset, 0.01),
                          "velocity": chord_velocity, "mute": False, "probability": 1.0,
                          "velocity_deviation": 0.0, "release_velocity": 64.0})
        written.append({"chord": item["chord"], "start": rnd(chord_start),
                        "duration": rnd(chord_duration), "pitches": pitches,
                        "notes": [midi_to_note(p, flats) for p in pitches]})
    end_time = max([s["start_time"] + s["duration"] for s in specs] or [cursor])
    obj, created = midi_clip_for_writing(ctx, track, slot, clip, as_bool(create, "create"),
                                         end_time)
    removed = 0
    if as_bool(clear, "clear") and not created:
        removed = _remove(obj, _select(_all_notes(obj), first_start, max(end_time, cursor)))
    extended = extend_clip_to(obj, end_time, ctx.song) if as_bool(extend, "extend") else None
    _ids, added, merged = _write_notes(obj, specs)
    result = _summary(ctx, obj)
    result.update({"added": added, "removed": removed, "chords": written})
    if key_cache:
        result["key"] = key_cache[0].label()
    if merged:
        result["merged"] = merged
    if created:
        result["created"] = True
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    return result


_ARP_STYLES = ("up", "down", "updown", "downup", "random", "chord", "root", "root_fifth",
               "octave", "pedal")


def _arp_sequence(style, pitches, root, octaves, rng):
    """One cycle of pitches for an arpeggio style."""
    base = sorted(pitches)
    stacked = []
    for octave in range(octaves):
        stacked.extend(p + 12 * octave for p in base)
    if style == "up":
        return [[p] for p in stacked]
    if style == "down":
        return [[p] for p in reversed(stacked)]
    if style == "updown":
        return [[p] for p in stacked + list(reversed(stacked))[1:-1]] or [[stacked[0]]]
    if style == "downup":
        down = list(reversed(stacked))
        return [[p] for p in down + stacked[1:-1]] or [[down[0]]]
    if style == "random":
        return [[rng.choice(stacked)] for _ in range(len(stacked))]
    if style == "chord":
        return [list(base)]
    if style == "root":
        return [[root]]
    if style == "root_fifth":
        return [[root], [root + 7]]
    if style == "octave":
        return [[root], [root + 12]]
    uppers = [p for p in stacked if p != root] or [root + 12]
    sequence = []
    for pitch in uppers:
        sequence.extend([[root], [pitch]])
    return sequence


@command("notes.write_arp", mutating=True,
         doc="Write an arpeggio or a simple bassline from chords (symbols, Roman numerals, "
             "degrees)")
def notes_write_arp(ctx, track=None, slot=None, clip=None, chords=None, key=None, start=0.0,
                    duration=None, rate="1/16", style="up", octaves=1, octave=3, gate=0.8,
                    velocity=100, accent_velocity=None, swing=0.0, seed=None, clear=True,
                    create=True, extend=True):
    """Arpeggiate a chord progression (or play its roots as a bassline).

    Args:
        track, slot / clip: which clip (created when missing, like write_chords).
        chords: like notes.write_chords — symbols ("Am7 F C G"), Roman numerals
            in ``key`` ("i VI III VII"), degrees ("1 6 4 5") or objects
            {"chord", "start"?, "duration"?, "octave"?}.
        key: "song" (default), "A minor", "F# dorian" ... (numerals/degrees).
        start: beat of the first chord; duration: beats per chord (default one bar).
        rate: step length — "1/16" (default), "1/8", "1/8T", 0.25 ...
        style: up, down, updown, downup, random (seeded), chord (repeated
            stabs), root (bassline: the root on every step), root_fifth,
            octave (root / root + 12), pedal (root alternating with the other
            chord tones).
        octaves: span of up/down styles (1..4).
        octave: octave of the chord root (C3 = 60); bass styles play the root there.
        gate: note length as a fraction of a step (0.05..1).
        velocity: 1..127; accent_velocity: velocity of each chord's first step.
        swing: 0..1 fraction of a step to delay every second step.
        seed: for style="random" (the seed used is returned).
        clear, create, extend: as in notes.write_chords.

    Returns:
        {"clip", "name", "length", "added", "removed", "style", "rate",
         "chords": [{"chord", "start", "duration", "notes": [...]}], "key"?,
         "seed"?, "created"?, "extended_to"?, "merged"?}
    """
    style = str(style).strip().lower()
    if style not in _ARP_STYLES:
        raise BridgeError("bad_args", "style must be one of %s" % ", ".join(_ARP_STYLES))
    items = _chord_items(chords)
    step = parse_grid(rate, "rate")
    default_duration = as_float(duration, "duration", allow_none=True) \
        if duration is not None else beats_per_bar(ctx.song)
    if default_duration <= 0:
        raise BridgeError("bad_args", "duration must be > 0")
    cursor = as_float(start, "start", minimum=0.0)
    octaves = as_int(octaves, "octaves", 1, 4)
    octave = as_int(octave, "octave", -1, 8)
    gate = as_float(gate, "gate", 0.05, 1.0)
    velocity = as_float(velocity, "velocity", 1, 127)
    accent = as_float(accent_velocity, "accent_velocity", 1, 127, allow_none=True)
    swing = as_float(swing, "swing", 0.0, 1.0)
    rng = None
    if style == "random":
        seed = random.randrange(1, 1 << 30) if seed is None else as_int(seed, "seed")
        rng = random.Random(seed)
    key_cache = []

    def key_getter():
        if not key_cache:
            key_cache.append(parse_key(ctx, key))
        return key_cache[0]

    specs, written = [], []
    first_start = cursor
    for index, item in enumerate(items):
        where = "chords[%d]" % index
        chord_start = as_float(item["start"], where + ".start", minimum=0.0) \
            if item.get("start") is not None else cursor
        chord_duration = as_float(item["duration"], where + ".duration") \
            if item.get("duration") is not None else default_duration
        if chord_duration <= 0:
            raise BridgeError("bad_args", "%s.duration must be > 0" % where)
        cursor = chord_start + chord_duration
        first_start = min(first_start, chord_start)
        parsed = chord_from_symbol(item["chord"], key_getter)
        if parsed is None:
            written.append({"chord": item["chord"], "start": rnd(chord_start),
                            "duration": rnd(chord_duration), "notes": []})
            continue
        root_pc, intervals, slash, flats = parsed
        chord_octave = as_int(item.get("octave", octave), where + ".octave", -1, 8)
        pitches = voice_chord(root_pc, intervals, chord_octave)
        base_root = (chord_octave + 2) * 12 + (slash if slash is not None else root_pc)
        sequence = _arp_sequence(style, pitches, base_root, octaves, rng)
        count = max(1, int(math.floor(chord_duration / step + 1e-9)))
        used = []
        for k in range(count):
            group = sequence[k % len(sequence)]
            time = chord_start + k * step + (swing * step if k % 2 == 1 else 0.0)
            length = min(step * gate, chord_start + chord_duration - time)
            if length <= 1e-4:
                continue
            for pitch in group:
                if not 0 <= pitch <= 127:
                    raise BridgeError("bad_args", "%s %r at octave %d leaves the MIDI range"
                                      % (where, item["chord"], chord_octave))
                specs.append({"pitch": int(pitch), "start_time": time, "duration": length,
                              "velocity": accent if (k == 0 and accent is not None)
                              else velocity, "mute": False, "probability": 1.0,
                              "velocity_deviation": 0.0, "release_velocity": 64.0})
                if pitch not in used:
                    used.append(pitch)
        written.append({"chord": item["chord"], "start": rnd(chord_start),
                        "duration": rnd(chord_duration),
                        "notes": [midi_to_note(p, flats) for p in used]})
    if not specs:
        raise BridgeError("bad_args", "nothing to write (only rests?)")
    end_time = max(sp["start_time"] + sp["duration"] for sp in specs)
    obj, created = midi_clip_for_writing(ctx, track, slot, clip, as_bool(create, "create"),
                                         max(end_time, cursor))
    removed = 0
    if as_bool(clear, "clear") and not created:
        removed = _remove(obj, _select(_all_notes(obj), first_start, max(end_time, cursor)))
    extended = extend_clip_to(obj, max(end_time, cursor), ctx.song) \
        if as_bool(extend, "extend") else None
    _ids, added, merged = _write_notes(obj, specs)
    result = _summary(ctx, obj)
    result.update({"added": added, "removed": removed, "style": style, "rate": rnd(step),
                   "chords": written})
    if key_cache:
        result["key"] = key_cache[0].label()
    if rng is not None:
        result["seed"] = seed
    if merged:
        result["merged"] = merged
    if created:
        result["created"] = True
    if extended is not None:
        result["extended_to"] = extended
        _timeline_hint(obj, result)
    return result


@command("notes.theory", doc="Note names <-> MIDI numbers, chord spelling (symbols, Roman "
                              "numerals, degrees) and the notes of a key")
def notes_theory(ctx, notes=None, chords=None, octave=3, key=None, scale=False):
    """Offline music-theory helper (does not touch the set).

    Args:
        notes: list of pitches (numbers, names or drum names) to convert.
        chords: list of chord symbols to spell — "Am7", "Bbsus2", "G/B", Roman
            numerals ("vi", "V7") or degrees ("1", "57") in ``key``.
        octave: root octave for the chord spelling (C3 = 60).
        key: "song" (default for numerals: the song's key), "A minor", "Bb",
            "F# dorian", {root, scale} — also sets flat/sharp spelling.
        scale: also list the key's scale notes (one octave from ``octave``).

    Returns:
        {"notes": [{"input", "pitch", "name", "in_key"?}], "chords": [{"chord",
         "pitches", "notes"}], "key"?: {"key", "root", "root_note", "scale",
         "intervals", "notes"?}}
    """
    result = {}
    parsed_key = parse_key(ctx, key) if key is not None or scale else None

    def key_getter():
        return parsed_key if parsed_key is not None else parse_key(ctx, None)

    flats_default = parsed_key.flats if parsed_key is not None else False
    if notes is not None:
        if not isinstance(notes, (list, tuple)):
            notes = [notes]
        converted = []
        for value in notes:
            pitch = note_to_midi(value)
            row = {"input": value, "pitch": pitch, "name": midi_to_note(pitch, flats_default)}
            if parsed_key is not None:
                row["in_key"] = parsed_key.contains(pitch)
            converted.append(row)
        result["notes"] = converted
    octave = as_int(octave, "octave", -1, 8)
    if chords is not None:
        if isinstance(chords, str):
            chords = [c for c in re.split(r"[\s|,]+", chords.strip()) if c]
        spelled = []
        for symbol in chords:
            parsed = chord_from_symbol(symbol, key_getter)
            if parsed is None:
                spelled.append({"chord": symbol, "pitches": [], "notes": []})
                continue
            pitches = voice_chord(parsed[0], parsed[1], octave)
            if parsed[2] is not None:
                low = pitches[0] - 1
                pitches = [low - ((low - parsed[2]) % 12)] + pitches
            spelled.append({"chord": symbol, "pitches": pitches,
                            "notes": [midi_to_note(p, parsed[3]) for p in pitches]})
        result["chords"] = spelled
    if parsed_key is not None:
        info = parsed_key.as_dict()
        if scale:
            base = (octave + 2) * 12 + parsed_key.root
            info["notes"] = [midi_to_note(base + i, parsed_key.flats)
                             for i in parsed_key.intervals]
        result["key"] = info
    if not result:
        raise BridgeError("bad_args", "pass notes, chords and/or key/scale")
    return result
