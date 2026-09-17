"""Analysis tools — Claude's ears and theory check: `live_theory_analyze` reads the notes of one
or more MIDI clips and reports key, chords (with Roman numerals), out-of-key notes and clashes
between parts; `live_audio_analyze` measures an audio file or audio clip (LUFS, peaks, spectral
balance, stereo, tempo, key, energy over time) and can compare it with a reference track.

The theory analysis is pure Python. The audio analysis runs on the machine of the LiveBridge MCP
server and needs the optional `audio` extra (numpy, scipy, soundfile, pyloudnorm); without it
the tool answers with the install command.
"""

from __future__ import annotations

import os
import re
from typing import Any

from . import bridge_call, tool_error
from .. import audio_analysis, theory
from ..client import BridgeClient
from .clips import address_args, clip_address
from .samples import _local_file

Ref = int | str

_KINDS = ("auto", "mix", "stem", "loop", "one_shot")
_NOTE_PAGE = 2000
_MAX_NOTES = 12000
_MAX_CLIPS = 16
_TRACK_PATH_RE = re.compile(r"^(song\.tracks\[\d+\])")
_DRUM_WORDS = ("drum", "kick", "snare", "hat", "perc", "clap", "808 kit", "909", "beat")
_KEY_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)\s*(.*)$")
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_MODE_ALIASES = {"": "major", "maj": "major", "major": "major", "ionian": "major",
                 "m": "minor", "min": "minor", "minor": "minor", "aeolian": "minor",
                 "natural minor": "minor"}
_AUDIO_TIMEOUT_HINT = "pass start=/duration= (seconds) to analyse a shorter window"


def _parse_key(text: str) -> dict[str, Any] | None:
    """"A minor", "F# dorian", "Bbm", "C" -> ``{"tonic", "mode"}`` (None when not a key)."""
    match = _KEY_RE.match(text)
    if not match:
        return None
    letter, accidental, rest = match.groups()
    tonic = _PITCH_CLASS[letter.upper()]
    if accidental in ("#", "♯"):
        tonic += 1
    elif accidental in ("b", "♭"):
        tonic -= 1
    mode = rest.strip().lower()
    mode = _MODE_ALIASES.get(mode, mode)
    if mode not in theory.SCALES:
        return None
    return {"tonic": tonic % 12, "mode": mode}


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and "error" in value and "type" in value


def register(mcp: Any, bridge: BridgeClient) -> None:
    """Register the analysis tools on the MCP app."""

    def fetch_notes(address: dict[str, Any]) -> Any:
        """All notes of one clip (paged) as the ``notes.get`` answer, or an error dict."""
        first: dict[str, Any] | None = None
        rows: list[Any] = []
        offset = 0
        while True:
            page = bridge_call(bridge, "notes.get",
                               dict(address, offset=offset, limit=_NOTE_PAGE))
            if _is_error(page) or not isinstance(page, dict):
                return page
            first = first or page
            rows.extend(page.get("notes") or [])
            offset = page.get("next_offset")
            if not offset or len(rows) >= _MAX_NOTES:
                break
        first = dict(first or {})
        first["notes"] = rows
        return first

    def track_of(clip_path: str, label: str) -> tuple[str, bool]:
        """``(track name, holds a Drum Rack)`` for a clip path (name-based guess as fallback)."""
        track_name = ""
        match = _TRACK_PATH_RE.match(clip_path or "")
        if match:
            listing = bridge_call(bridge, "devices.list", {"track": match.group(1)})
            if isinstance(listing, dict) and not _is_error(listing):
                track_name = str((listing.get("track") or {}).get("name") or "")
                if any(isinstance(d, dict) and (d.get("can_have_drum_pads") or
                                                "drum" in str(d.get("class_name", "")).lower())
                       for d in listing.get("devices") or []):
                    return track_name, True
        text = f"{label} {track_name}".lower()
        return track_name, any(word in text for word in _DRUM_WORDS)

    @mcp.tool()
    def live_theory_analyze(
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        clips: list[Any] | None = None,
        key: str | None = None,
        segment: float | None = None,
        drums: str = "auto",
    ) -> Any:
        """Music-theory check of one or more MIDI clips: which key they are in, the chord
        progression (names + Roman numerals), notes outside the key, clashes between parts
        (semitone rubs, mud in the low register) and per-part statistics with suggestions.
        Read-only. Use it before adding a part to existing material ("what key is this?"),
        and after writing parts to verify that bass, chords and lead agree.

        Args:
            track, slot / clip: one clip (same addressing as the `live_clip_*` tools).
            clips: or several clips analysed TOGETHER (that is what finds clashes between
                bass and chords): `[{"track": "Bass", "slot": 0}, {"track": "Pad", "slot": 0},
                "song.tracks[3].clip_slots[0].clip"]` (max 16). Clips are aligned at beat 0.
            key: force the key instead of detecting it — "A minor", "F# dorian", "Bbm", or
                "song" for the scale set in Live. Modes: major, minor, dorian, phrygian,
                lydian, mixolydian, harmonic minor.
            segment: chord window in beats (default: one bar, half a bar when chords change
                faster).
            drums: "auto" (clips on a Drum Rack track only get statistics), "include" (treat
                every clip as pitched) or "skip" (leave drum clips out).

        Returns:
            `{key:{name, tonic, mode, confidence}, key_candidates:[{name, score}],
            scale_notes, song_scale? (Live's own scale setting when it differs), chords:[{at
            (beat), bar, name, roman, confidence, extra?}], progression ("i VI III VII" — the
            shortest repeating unit, usable in live_clip_write_chords), out_of_key:[{part,
            note, count, beats, at}], clashes:[{kind: "semitone"|"low_mud", parts, notes, at,
            beats}], parts:[{name, notes, range, polyphony, velocity:[min,max],
            notes_per_bar, drums?}], suggestions:[...]}`

        Gotchas: relative major/minor share all notes — the bass and the opening note break
        the tie, `key_candidates` shows the runner-up; confidence below ~0.35 means "ask the
        user or look at the bass". One-note basslines and drum clips alone have no key.
        Roman numerals are scale degrees of the detected scale (in A minor F = VI, not bVI).
        """
        cmd = "notes.get"
        if drums not in ("auto", "include", "skip"):
            return tool_error("drums must be 'auto', 'include' or 'skip'", cmd=cmd)
        if segment is not None and segment <= 0:
            return tool_error("segment must be > 0 beats", cmd=cmd)
        addresses: list[dict[str, Any]] = []
        if clips is not None:
            if track is not None or slot is not None or clip is not None:
                return tool_error("pass either clips=[...] or one track+slot / clip", cmd=cmd)
            if not clips or len(clips) > _MAX_CLIPS:
                return tool_error(f"clips must hold 1..{_MAX_CLIPS} clip addresses", cmd=cmd)
            for item in clips:
                if isinstance(item, str):
                    error = clip_address(None, None, item, cmd)
                    address = address_args(None, None, item)
                elif isinstance(item, dict) and set(item) <= {"track", "slot", "clip"}:
                    error = clip_address(item.get("track"), item.get("slot"),
                                         item.get("clip"), cmd)
                    address = address_args(item.get("track"), item.get("slot"),
                                           item.get("clip"))
                else:
                    return tool_error("each entry of clips must be a clip path/name or "
                                      "{'track':..., 'slot':...}", cmd=cmd)
                if error:
                    return error
                addresses.append(address)
        else:
            error = clip_address(track, slot, clip, cmd)
            if error:
                return error
            addresses.append(address_args(track, slot, clip))

        forced = None
        song_scale = None
        state = bridge_call(bridge, "transport.get")
        beats_per_bar = 4.0
        if isinstance(state, dict) and not _is_error(state):
            match = re.match(r"^(\d+)/(\d+)$", str(state.get("signature", "")))
            if match and int(match.group(2)):
                beats_per_bar = int(match.group(1)) * 4.0 / int(match.group(2))
            scale = state.get("scale") if isinstance(state.get("scale"), dict) else None
            if scale and scale.get("root") and scale.get("name"):
                song_scale = f"{scale['root']} {str(scale['name']).lower()}"
        if key is not None:
            wanted = song_scale if key.strip().lower() == "song" else key
            forced = _parse_key(wanted) if wanted else None
            if forced is None:
                return tool_error(
                    "key must look like 'A minor', 'F# dorian', 'Bbm' or 'song' (modes: "
                    + ", ".join(theory.SCALES) + ")"
                    + ("" if wanted else " — Live reports no song scale"), cmd=cmd)

        parts = []
        for address in addresses:
            answer = fetch_notes(address)
            if _is_error(answer) or not isinstance(answer, dict):
                return answer
            path = str(answer.get("clip") or "")
            label = str(answer.get("name") or "")
            track_name, drum_clip = track_of(path, label)
            if drums == "include":
                drum_clip = False
            if drum_clip and drums == "skip":
                continue
            name = " / ".join(filter(None, (track_name, label))) or path
            if any(p["name"] == name for p in parts):
                name = f"{name} ({path})"
            parts.append({"name": name, "notes": answer.get("notes") or [],
                          "length": answer.get("length") or 0.0, "drums": drum_clip})
        result = theory.analyze_parts(parts, beats_per_bar=beats_per_bar, segment=segment,
                                      key=forced)
        detected = (result.get("key") or {}).get("name")
        if song_scale and detected and forced is None and \
                _parse_key(song_scale) != _parse_key(detected):
            result["song_scale"] = song_scale
            result["suggestions"].append(
                f"Live's scale setting is {song_scale} but the notes say {detected}: set the "
                "song key (live_transport_set) so fit_scale, Roman-numeral chords and Live's "
                "scale highlighting work in the right key.")
        return result

    @mcp.tool()
    def live_audio_analyze(
        file_path: str | None = None,
        track: Ref | None = None,
        slot: Ref | None = None,
        clip: str | None = None,
        reference: str | None = None,
        kind: str = "auto",
        start: float | None = None,
        duration: float | None = None,
        sections: bool = True,
    ) -> Any:
        """Listen to audio by measuring it: loudness (LUFS, true peak, crest factor, loudness
        range, clipping), spectral balance in 8 bands with mix flags (mud, harshness, missing
        sub ...), stereo width and mono compatibility, tempo, key and the energy over time —
        of an audio file, an audio clip in the set (e.g. a `live_record_resample` bounce or a
        Splice loop), optionally compared with a reference track. Read-only.

        Args:
            file_path: an audio file (WAV, AIFF, FLAC, OGG, MP3) on the machine running the
                LiveBridge MCP server.
            track, slot / clip: or an audio clip in Live — its sample file is analysed (the
                whole file, not only the clip's loop region).
            reference: path of a reference track to compare with (same measurements, plus
                `comparison` with the differences).
            kind: "auto" (by length), "mix" (full track: spectral targets and loudness advice
                apply), "stem" / "loop" / "one_shot" (measurements without mix targets).
            start, duration: analyse a window in seconds (default: from 0, at most 10 minutes).
            sections: include `energy` (RMS over time, up to 32 steps).

        Returns:
            `{file:{path, name, duration, sample_rate, channels, format, analysed?}, kind,
            loudness:{lufs, peak_db, true_peak_db, rms_db, crest_db, loudness_range_lu?,
            short_term_max_lufs?, clipped_samples?}, spectrum:{bands_db:{sub (20-60 Hz), bass
            (60-120), low_mid (120-250), mud (250-500), mid (500-2k), presence (2-5k), high
            (5-10k), air (10-20k)} (dB relative to the total), centroid_hz}, stereo:
            {correlation, width, low_end_width} | {mono}, tempo?:{bpm, confidence,
            also_possible}, key?:{name, confidence, candidates}, energy?:{start_s, step_s,
            rms_db:[...]}, flags:[plain-language observations], reference?, comparison?:
            {lufs, crest_db, bands_db, width, notes}}`

        Gotchas: this is measurement, not taste — flags are hints against typical full-mix
        values, a sparse intro or a single stem legitimately deviates. Tempo can land on
        half/double time (`also_possible`); key detection on drums-only material is
        meaningless (low confidence). To hear the current set: bounce it first with
        `live_record_resample`, then pass that track/slot here. In LAN mode a clip's file
        lives on the Live machine and cannot be read from here. Needs the `audio` extra.
        """
        cmd = "clips.get"
        if kind not in _KINDS:
            return tool_error(f"kind must be one of {', '.join(_KINDS)}", cmd=cmd)
        if start is not None and start < 0:
            return tool_error("start must be >= 0 seconds", cmd=cmd)
        if duration is not None and duration <= 0:
            return tool_error("duration must be > 0 seconds", cmd=cmd)
        by_clip = track is not None or slot is not None or clip is not None
        if (file_path is None) == (not by_clip):
            return tool_error("pass either file_path or an audio clip (track + slot / clip)",
                              cmd=cmd)
        clip_info = None
        if by_clip:
            error = clip_address(track, slot, clip, cmd)
            if error:
                return error
            answer = bridge_call(bridge, cmd, address_args(track, slot, clip))
            if _is_error(answer) or not isinstance(answer, dict):
                return answer
            file_path = answer.get("file_path")
            if not file_path:
                return tool_error(f"{answer.get('name') or 'that clip'} is not an audio clip "
                                  "(no sample file) — MIDI clips go to live_theory_analyze; "
                                  "bounce the track with live_record_resample to hear it",
                                  type="invalid_state", cmd=cmd)
            clip_info = {"path": answer.get("path"), "name": answer.get("name")}
        local = _local_file(file_path or "")
        if local is None:
            return tool_error(f"{file_path} does not exist on this machine (the one running "
                              "the LiveBridge MCP server)", type="not_found", cmd=cmd)
        paths = [local]
        if reference is not None:
            ref_local = _local_file(reference)
            if ref_local is None:
                return tool_error(f"reference {reference} does not exist on this machine",
                                  type="not_found", cmd=cmd)
            paths.append(ref_local)
        results = []
        for index, path in enumerate(paths):
            try:
                results.append(audio_analysis.analyze_file(
                    path, start=start or 0.0, duration=duration,
                    kind=kind if index == 0 else "mix", sections=sections and index == 0))
            except audio_analysis.MissingDependency as exc:
                return tool_error(str(exc), type="unsupported", cmd=cmd)
            except MemoryError:
                return tool_error(f"not enough memory to analyse {os.path.basename(path)} — "
                                  + _AUDIO_TIMEOUT_HINT, type="invalid_state", cmd=cmd)
            except Exception as exc:  # unreadable / unsupported file: soundfile raises its own
                return tool_error(f"could not analyse {os.path.basename(path)}: {exc}",
                                  type="invalid_state", cmd=cmd)
        result = results[0]
        if clip_info is not None:
            result["clip"] = clip_info
        if len(results) > 1:
            other = results[1]
            result["reference"] = {k: other[k] for k in ("file", "loudness", "spectrum",
                                                         "stereo", "tempo", "key")
                                   if k in other}
            result["comparison"] = audio_analysis.compare(result, other)
        return result
