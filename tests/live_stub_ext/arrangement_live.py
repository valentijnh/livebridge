"""Stub extension: arrangement behaviour measured on Live 12.4.5 (g2 fixer, 2026-09-10).

What the shared stub does not model yet, all verified on the running Live 12.4.5 with scratch
objects:

* ``Track.duplicate_clip_to_arrangement`` copies the clip's **envelopes** (session ->
  arrangement and arrangement -> arrangement) — on a MIDI track without devices.  With an
  instrument on the track Live 12.4.5 copied none (cross fixer, 2026-09-10, Session view
  focused): ``install(Live, copy_envelopes=...)`` picks ``True`` (always, the default),
  ``False`` (never) or ``"without_devices"`` (the measured rule).  On the arrangement copy
  ``automation_envelope(p)`` still returns None, but ``automation_envelopes`` lists the copied
  envelopes, and ``value_at_time`` / ``insert_step`` / ``create_event`` /
  ``delete_events_in_range`` / ``clear_envelope`` / ``clear_all_envelopes`` work on them;
  ``create_automation_envelope`` raises "Not a session clip or parameter belongs to another
  track." (the shared stub already raises and returns None there).
* Duplicating an arrangement clip keeps its timeline length (a looped clip stretched to 16
  beats with a 4-beat loop copies as 16 beats, the loop repeating inside), where the shared
  stub used the loop length.
* Lengthening an arrangement clip on the timeline (looping off, ``loop_end``/``end_marker`` to
  the new end, looping on again) stops at the start of the next clip on the same track: the
  clip's ``end_time`` is capped there instead of cutting the next clip.

* ``Envelope.events_in_range`` / ``delete_events_in_range`` raise ``ValueError("Range out of
  bounds.")`` when the range reaches beyond +-1576800 beats (the arrangement time limit).

``install(Live)`` patches ``Live.Track.Track.duplicate_clip_to_arrangement``, the
``Live.Clip.Clip.looping`` property and the two Envelope range methods, and returns
``uninstall()``.
"""

LIMIT = 1576800.0


def install(Live, copy_envelopes=True):
    """Patch the stub; returns a function that restores it.

    ``copy_envelopes``: ``True`` (copies always carry the envelopes), ``False`` (never) or
    ``"without_devices"`` (only when the destination track has no devices — Live 12.4.5).
    """
    if copy_envelopes not in (True, False, "without_devices"):
        raise ValueError("copy_envelopes must be True, False or 'without_devices'")
    model = Live._model
    track_cls = model.Track
    clip_cls = model.Clip
    envelope_cls = model.Envelope
    original_duplicate = track_cls.duplicate_clip_to_arrangement
    original_looping = clip_cls.looping
    original_events = envelope_cls.events_in_range
    original_delete = envelope_cls.delete_events_in_range

    def _bounded(method):
        def checked(self, start_time, end_time, /):
            if not -LIMIT <= float(start_time) <= LIMIT or not -LIMIT <= float(end_time) <= LIMIT:
                raise ValueError("Range out of bounds.")
            return method(self, start_time, end_time)
        return checked

    def duplicate_clip_to_arrangement(self, clip, destination_time):
        clone = original_duplicate(self, clip, destination_time)
        if getattr(clip, "_is_arrangement", False) and clip._looping:
            clone._arrangement_length = clip.end_time - clip.start_time
        clone._envelopes = {}
        if copy_envelopes is False or (copy_envelopes == "without_devices"
                                       and list(getattr(self, "_devices", []) or [])):
            return clone
        for key, envelope in list(getattr(clip, "_envelopes", {}).items()):
            copy = envelope_cls(envelope._parameter, clone)
            copy._points = [list(point) for point in envelope._points]
            clone._envelopes[key] = copy
        return clone

    def set_looping(self, value):
        original_looping.fset(self, value)
        if not (self._is_arrangement and self._looping):
            return
        owner = self._canonical_parent
        clips = getattr(owner, "_arrangement_clips", None) or []
        later = [c._start_time for c in clips if c is not self and
                 c._start_time > self._start_time + 1e-9]
        if later:
            limit = min(later) - self._start_time
            if self._arrangement_length > limit:
                self._arrangement_length = limit
                brace_start = getattr(self, "_unlooped_brace", (self._start_marker,))[0]
                self._end_marker = min(self._end_marker, brace_start + limit)

    track_cls.duplicate_clip_to_arrangement = duplicate_clip_to_arrangement
    clip_cls.looping = property(original_looping.fget, set_looping)
    envelope_cls.events_in_range = _bounded(original_events)
    envelope_cls.delete_events_in_range = _bounded(original_delete)

    def uninstall():
        track_cls.duplicate_clip_to_arrangement = original_duplicate
        clip_cls.looping = original_looping
        envelope_cls.events_in_range = original_events
        envelope_cls.delete_events_in_range = original_delete

    return uninstall
