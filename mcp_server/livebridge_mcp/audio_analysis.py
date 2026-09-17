"""Audio file analysis for ``live_audio_analyze`` — the closest thing Claude has to ears.

Measures what a producer listens for: loudness (LUFS, peaks, dynamics, clipping), spectral
balance per band (sub ... air) with mix flags, stereo width / mono compatibility, tempo, key and
an energy curve over time (where the drops and breakdowns are). Optionally compares against a
reference track.

Needs the optional ``audio`` extra (``pip install "livebridge-mcp[audio]"``: numpy, scipy,
soundfile, pyloudnorm) — deliberately no librosa/numba, so it stays light on an 8 GB laptop and
installs the same way on Windows and macOS. Imports are lazy: the MCP server starts without them
and the tool answers with the install command.
"""

from __future__ import annotations

import math
import os
from typing import Any

from .theory import NOTE_NAMES, detect_key

__all__ = ["MissingDependency", "analyze_file", "compare", "INSTALL_HINT"]

INSTALL_HINT = ('pip install "livebridge-mcp[audio]"  (or: pip install numpy scipy soundfile '
                'pyloudnorm) into the Python environment that runs the LiveBridge MCP server')

#: Longest stretch analysed in one go (seconds) — bounds memory (stereo float32 ~ 21 MB/min).
MAX_SECONDS = 600.0

#: Spectral bands (Hz) and what a balanced full mix roughly carries in each, in dB relative to
#: the total (pink-ish tilt, club/pop masters). Used for flags only — a single stem or a sparse
#: intro legitimately deviates.
BANDS: tuple[tuple[str, float, float, float], ...] = (
    ("sub", 20, 60, -9.0), ("bass", 60, 120, -6.5), ("low_mid", 120, 250, -8.5),
    ("mud", 250, 500, -11.0), ("mid", 500, 2000, -11.5), ("presence", 2000, 5000, -17.0),
    ("high", 5000, 10000, -21.0), ("air", 10000, 20000, -27.0),
)
_ANALYSIS_RATE = 22050


class MissingDependency(RuntimeError):
    """An optional audio library is not installed."""


def _deps() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        import pyloudnorm
        import scipy.signal as signal
        import soundfile as sf
    except ImportError as exc:
        raise MissingDependency(f"audio analysis needs {exc.name}: {INSTALL_HINT}") from exc
    return np, signal, sf, pyloudnorm


def _db(value: float, floor: float = -120.0) -> float:
    return round(max(20.0 * math.log10(value), floor), 1) if value > 0 else floor


def _load(path: str, start: float, duration: float | None) -> tuple[Any, int, dict[str, Any]]:
    np, _signal, sf, _pyln = _deps()
    info = sf.info(path)
    rate = info.samplerate
    total = info.frames / float(rate) if rate else 0.0
    first = int(max(start, 0.0) * rate)
    wanted = min(duration or MAX_SECONDS, MAX_SECONDS)
    frames = max(min(int(wanted * rate), info.frames - first), 0)
    data = sf.read(path, start=first, frames=frames, dtype="float32", always_2d=True)[0]
    meta = {"path": path, "name": os.path.basename(path), "duration": round(total, 2),
            "sample_rate": rate, "channels": info.channels, "format": info.format,
            "subtype": info.subtype}
    if first or frames < info.frames - first:
        meta["analysed"] = [round(first / rate, 2), round((first + frames) / rate, 2)]
    return data, rate, meta


def _loudness(data: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, pyln = _deps()
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if data.size else 0.0
    result: dict[str, Any] = {"peak_db": _db(peak), "rms_db": _db(rms)}
    if peak > 0:
        # true peak: 4x oversample one second around the loudest sample
        index = int(np.argmax(np.max(np.abs(data), axis=1)))
        lo, hi = max(index - rate // 2, 0), min(index + rate // 2, len(data))
        over = signal.resample_poly(data[lo:hi], 4, 1, axis=0)
        result["true_peak_db"] = _db(max(float(np.max(np.abs(over))), peak))
        result["crest_db"] = round(result["peak_db"] - result["rms_db"], 1)
    clipped = int(np.sum(np.abs(data) >= 0.999))
    if clipped:
        result["clipped_samples"] = clipped
    seconds = len(data) / float(rate)
    if seconds >= 0.5 and rms > 0:
        meter = pyln.Meter(rate)
        result["lufs"] = round(float(meter.integrated_loudness(data)), 1)
        if seconds >= 9.0:
            # short-term (3 s) loudness spread = how much the track breathes
            window, hop = 3 * rate, rate
            short = [float(meter.integrated_loudness(data[i:i + window]))
                     for i in range(0, len(data) - window + 1, hop)]
            short = [v for v in short if v > -70.0]
            if len(short) >= 4:
                low, high = np.percentile(short, [10, 95])
                result["loudness_range_lu"] = round(float(high - low), 1)
                result["short_term_max_lufs"] = round(max(short), 1)
    return result


def _spectrum(mono: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, _pyln = _deps()
    nper = min(8192, len(mono))
    if nper < 256:
        return {}
    freqs, psd = signal.welch(mono, fs=rate, nperseg=nper)
    total = float(np.sum(psd[(freqs >= 20) & (freqs <= 20000)]))
    if total <= 0:
        return {}
    bands = {}
    for name, low, high, _target in BANDS:
        if low >= rate / 2:
            continue
        part = float(np.sum(psd[(freqs >= low) & (freqs < high)]))
        bands[name] = round(10.0 * math.log10(part / total), 1) if part > 0 else -90.0
    audible = (freqs >= 20) & (freqs <= 20000)
    centroid = float(np.sum(freqs[audible] * psd[audible]) / total)
    return {"bands_db": bands, "centroid_hz": round(centroid)}


def _stereo(data: Any, rate: int) -> dict[str, Any]:
    np, signal, _sf, _pyln = _deps()
    if data.shape[1] < 2:
        return {"mono": True}
    left, right = data[:, 0].astype(np.float64), data[:, 1].astype(np.float64)
    mid, side = (left + right) / 2.0, (left - right) / 2.0
    mid_power, side_power = float(np.mean(mid ** 2)), float(np.mean(side ** 2))
    denom = math.sqrt(float(np.mean(left ** 2)) * float(np.mean(right ** 2)))
    result: dict[str, Any] = {
        "correlation": round(float(np.mean(left * right)) / denom, 2) if denom else 1.0,
        "width": round(side_power / (mid_power + side_power), 2)
        if mid_power + side_power > 0 else 0.0,
    }
    if len(mid) > 4096:
        sos = signal.butter(4, 120.0, btype="low", fs=rate, output="sos")
        low_mid = float(np.mean(signal.sosfilt(sos, mid) ** 2))
        low_side = float(np.mean(signal.sosfilt(sos, side) ** 2))
        if low_mid + low_side > 0:
            result["low_end_width"] = round(low_side / (low_mid + low_side), 2)
    return result


def _stft(mono: Any, rate: int, nperseg: int, hop: int) -> tuple[Any, Any]:
    np, signal, _sf, _pyln = _deps()
    freqs, _times, spec = signal.stft(mono, fs=rate, nperseg=nperseg, noverlap=nperseg - hop,
                                      boundary=None, padded=False)
    return freqs, np.abs(spec)


def _tempo(magnitudes: Any, rate: int, hop: int) -> dict[str, Any]:
    """Tempo from the spectral-flux onset envelope (autocorrelation, prior around 120 BPM)."""
    np, _signal, _sf, _pyln = _deps()
    if magnitudes.shape[1] < 64:
        return {}
    logmag = np.log1p(100.0 * magnitudes)
    flux = np.sum(np.maximum(np.diff(logmag, axis=1), 0.0), axis=0)
    flux = flux - np.mean(flux)
    if not np.any(flux):
        return {}
    frame_rate = rate / float(hop)
    corr = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    corr = corr / (corr[0] or 1.0)
    lags = np.arange(1, len(corr))
    bpms = 60.0 * frame_rate / lags
    valid = (bpms >= 60) & (bpms <= 200)
    if not np.any(valid):
        return {}
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = corr[1:] * prior
    best = int(np.argmax(np.where(valid, score, -np.inf)))
    # parabolic refinement of the autocorrelation peak
    lag = float(lags[best])
    if 0 < best < len(score) - 1:
        a, b, c = corr[best], corr[best + 1], corr[best + 2]
        shift = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) else 0.0
        lag += max(min(shift, 0.5), -0.5)
    bpm = float(60.0 * frame_rate / lag)
    strength = float(corr[best + 1])
    result = {"bpm": round(bpm, 1), "confidence": round(max(0.0, min(1.0, strength * 2.5)), 2)}
    alternatives = [round(v, 1) for v in (bpm / 2.0, bpm * 2.0) if 50 <= v <= 220]
    if alternatives:
        result["also_possible"] = alternatives
    return result


def _key(freqs: Any, magnitudes: Any) -> dict[str, Any]:
    np, _signal, _sf, _pyln = _deps()
    usable = (freqs >= 55.0) & (freqs <= 2000.0)
    if not np.any(usable):
        return {}
    power = np.mean(magnitudes[usable] ** 2, axis=1)
    midi = 69.0 + 12.0 * np.log2(freqs[usable] / 440.0)
    classes = np.mod(np.rint(midi).astype(int), 12)
    chroma = [float(np.sum(power[classes == pc])) for pc in range(12)]
    low = (freqs >= 40.0) & (freqs <= 160.0)
    bass = None
    if np.any(low):
        low_power = np.mean(magnitudes[low] ** 2, axis=1)
        low_classes = np.mod(np.rint(69.0 + 12.0 * np.log2(freqs[low] / 440.0)).astype(int), 12)
        bass = [float(np.sum(low_power[low_classes == pc])) for pc in range(12)]
    ranked = detect_key(chroma, bass)
    if not ranked:
        return {}
    top = ranked[0]
    margin = top["score"] - ranked[1]["score"]
    return {"name": f"{NOTE_NAMES[top['tonic']]} {top['mode']}",
            "confidence": round(max(0.0, min(1.0, 0.4 * top["score"] + 3.0 * margin)), 2),
            "candidates": [f"{NOTE_NAMES[c['tonic']]} {c['mode']}" for c in ranked[:3]]}


def _energy_curve(mono: Any, rate: int, offset: float, points: int = 32) -> dict[str, Any]:
    """RMS over time in ``points`` equal steps (dB) — shows intro / build / drop / breakdown."""
    np, _signal, _sf, _pyln = _deps()
    seconds = len(mono) / float(rate)
    if seconds < 8.0:
        return {}
    count = int(min(points, seconds // 2))
    step = len(mono) // count
    values = [_db(float(np.sqrt(np.mean(mono[i * step:(i + 1) * step] ** 2)))) for i in range(count)]
    return {"step_s": round(step / float(rate), 2), "start_s": round(offset, 2),
            "rms_db": values}


def _flags(result: dict[str, Any], kind: str) -> list[str]:
    """Plain-language mix observations from the measurements."""
    flags: list[str] = []
    loud = result.get("loudness", {})
    lufs = loud.get("lufs")
    if loud.get("clipped_samples"):
        flags.append(f"{loud['clipped_samples']} samples at full scale — the file clips: lower "
                     "the level before the limiter / the master.")
    elif loud.get("true_peak_db", -99) > -0.3:
        flags.append(f"True peak {loud['true_peak_db']} dBTP leaves no headroom (aim for -1 dBTP "
                     "on a master, around -6 dB on a mix that still gets mastered).")
    if kind == "mix":
        if lufs is not None and lufs > -7.0:
            flags.append(f"{lufs} LUFS is very loud — expect pumping/distortion; club masters "
                         "sit around -8 to -6, streaming around -14 to -9.")
        elif lufs is not None and lufs < -18.0:
            flags.append(f"{lufs} LUFS is quiet for a finished track (fine for an unmastered mix "
                         "with headroom).")
        if loud.get("crest_db") is not None and loud["crest_db"] < 7.0:
            flags.append(f"Crest factor {loud['crest_db']} dB: heavily limited — transients "
                         "(kick, snare) lose punch below ~8 dB.")
        bands = result.get("spectrum", {}).get("bands_db", {})
        targets = {name: target for name, _l, _h, target in BANDS}
        notes = {
            ("mud", 1): "a build-up around 250-500 Hz (muddy/boxy): cut pads, chords and reverb "
                        "returns there, high-pass what is not kick or bass",
            ("low_mid", 1): "a lot of 120-250 Hz (boomy): bass and kick tail overlap or the "
                            "chords sit too low",
            ("sub", 1): "very heavy sub (20-60 Hz): check the kick/808 level and high-pass "
                        "everything else at 30 Hz+",
            ("sub", -1): "little sub energy below 60 Hz: the kick/bass lacks weight (or the "
                         "track is not meant to have sub)",
            ("bass", -1): "thin 60-120 Hz: the bass/kick body is weak",
            ("presence", 1): "a lot of 2-5 kHz (harsh/fatiguing): tame leads, vocals or hats",
            ("presence", -1): "little 2-5 kHz: the mix sounds distant — leads/vocals lack "
                              "presence",
            ("high", -1): "dull top (5-10 kHz): hats/air missing or too much low-pass",
            ("air", 1): "very bright above 10 kHz: hissy hats or excited highs",
        }
        for (name, sign), text in notes.items():
            if name in bands and (bands[name] - targets[name]) * sign > 5.0:
                flags.append(f"Spectrum: {text} ({name} {bands[name]} dB vs ~{targets[name]}).")
    stereo = result.get("stereo", {})
    if stereo.get("correlation", 1.0) < 0.1:
        flags.append(f"Stereo correlation {stereo['correlation']}: parts cancel in mono (club "
                     "systems, phones) — reduce widening or fix phase.")
    if stereo.get("low_end_width", 0.0) > 0.15:
        flags.append("The low end (<120 Hz) is wide: keep kick and bass mono (Utility -> Bass "
                     "Mono) for a solid centre.")
    return flags


def analyze_file(path: str, start: float = 0.0, duration: float | None = None,
                 kind: str = "auto", sections: bool = True) -> dict[str, Any]:
    """Measure one audio file.

    Args:
        path: a file soundfile can read (WAV, AIFF, FLAC, OGG, MP3).
        start, duration: analyse a window in seconds (default: the first 10 minutes).
        kind: "mix" (full track: mix flags apply), "stem"/"loop"/"one_shot" (no spectral
            targets) or "auto" (one_shot below 2 s, loop below 20 s, else mix).
        sections: include the energy curve over time.

    Raises:
        MissingDependency: the audio extra is not installed.
        OSError / RuntimeError: the file cannot be read.
    """
    np, signal, _sf, _pyln = _deps()
    data, rate, meta = _load(path, start, duration)
    if not len(data):
        raise ValueError("the file (or the chosen window) contains no audio")
    seconds = len(data) / float(rate)
    if kind == "auto":
        kind = "one_shot" if seconds < 2.0 else "loop" if seconds < 20.0 else "mix"
    result: dict[str, Any] = {"file": meta, "kind": kind}
    mono = np.mean(data, axis=1, dtype=np.float64)
    if not np.any(mono) and not np.any(data):
        result["silent"] = True
        result["flags"] = ["The file is digital silence."]
        return result
    result["loudness"] = _loudness(data, rate)
    result["spectrum"] = _spectrum(mono, rate)
    result["stereo"] = _stereo(data, rate)
    if seconds >= 2.0:
        factor = math.gcd(rate, _ANALYSIS_RATE)
        small = signal.resample_poly(mono, _ANALYSIS_RATE // factor, rate // factor) \
            if rate != _ANALYSIS_RATE else mono
        if seconds >= 4.0:
            _freqs, mags = _stft(small, _ANALYSIS_RATE, 1024, 256)
            tempo = _tempo(mags[:, :int(90 * _ANALYSIS_RATE / 256)], _ANALYSIS_RATE, 256)
            if tempo:
                result["tempo"] = tempo
        freqs, mags = _stft(small, _ANALYSIS_RATE, 8192, 2048)
        key = _key(freqs, mags)
        if key:
            result["key"] = key
    if sections:
        curve = _energy_curve(mono, rate, max(start, 0.0))
        if curve:
            result["energy"] = curve
    result["flags"] = _flags(result, kind)
    return result


def compare(mix: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """Differences mix - reference (loudness, bands, width) with suggestions."""
    diff: dict[str, Any] = {}
    notes: list[str] = []
    for field in ("lufs", "crest_db", "loudness_range_lu"):
        a, b = mix.get("loudness", {}).get(field), reference.get("loudness", {}).get(field)
        if a is not None and b is not None:
            diff[field] = round(a - b, 1)
    bands_a = mix.get("spectrum", {}).get("bands_db", {})
    bands_b = reference.get("spectrum", {}).get("bands_db", {})
    ranges = {name: (low, high) for name, low, high, _t in BANDS}
    band_diff = {name: round(bands_a[name] - bands_b[name], 1)
                 for name in bands_a if name in bands_b}
    if band_diff:
        diff["bands_db"] = band_diff
        for name, delta in sorted(band_diff.items(), key=lambda item: -abs(item[1])):
            if abs(delta) >= 3.0:
                low, high = ranges[name]
                notes.append(f"{name} ({int(low)}-{int(high)} Hz) is {abs(delta)} dB "
                             f"{'louder' if delta > 0 else 'quieter'} than the reference.")
    a, b = mix.get("stereo", {}).get("width"), reference.get("stereo", {}).get("width")
    if a is not None and b is not None:
        diff["width"] = round(a - b, 2)
        if abs(a - b) >= 0.1:
            notes.append(f"The mix is {'wider' if a > b else 'narrower'} than the reference "
                         f"(width {a} vs {b}).")
    if "lufs" in diff and abs(diff["lufs"]) >= 2.0:
        notes.append(f"The mix is {abs(diff['lufs'])} LU "
                     f"{'louder' if diff['lufs'] > 0 else 'quieter'} than the reference — "
                     "level-match before judging the tone (band values are already relative).")
    diff["notes"] = notes
    return diff
