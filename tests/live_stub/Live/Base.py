"""``Live.Base`` — vectors, ``Timer`` and ``LimitationError``."""

from ._model import LimitationError, Timer, Vector  # noqa: F401

#: The other real vector types behave like read-only sequences.
FloatVector = IntVector = IntU64Vector = StringVector = ObjectVector = Vector


def log(message):
    """``Live.Base.log(str)`` — the stub drops the message."""
    return None
