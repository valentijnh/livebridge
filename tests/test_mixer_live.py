"""Mixer dB handling checked against real Live 12.4.5 (T2 live test).

Live's own ``str_for_value`` is the final word on what a fader shows. The fitted curves in
``handlers/mixer.py`` are within ~0.2 dB of it (worst below -60 dB), so absolute and relative
dB writes are refined against the parameter's display: on the running Live "-3 dB" now shows
"-3.0 dB" instead of "-2.998 dB" and "-65 dB" shows "-65.0 dB" instead of "-64.869 dB".
"""

import pytest

from LiveBridge.handlers import mixer


class FakeFader(object):
    """A parameter whose display is Live-like but offset from the fitted curve."""

    def __init__(self, offset=0.13, send=False, value=0.85):
        self.min, self.max, self.value = 0.0, 1.0, value
        self.offset, self.send = offset, send

    def str_for_value(self, value):
        db = mixer.volume_to_db(value, self.send)
        if db == float("-inf"):
            return "-inf dB"
        return "%.3f dB" % (db + self.offset)


def test_refine_db_lands_on_the_display():
    fader = FakeFader()
    for target in (-3.0, -12.0, -40.0, -65.0):
        guess = mixer.db_to_volume(target)
        refined = mixer.refine_db(fader, guess, target)
        assert float(fader.str_for_value(refined).split()[0]) == pytest.approx(target, abs=2e-3)


def test_refine_db_handles_inf_bracket_and_unreadable_displays():
    fader = FakeFader(offset=0.0)
    low = mixer.db_to_volume(-69.5)
    assert mixer.refine_db(fader, low, -69.5) == pytest.approx(low, abs=1e-3)

    class NoDb(FakeFader):
        def str_for_value(self, value):
            return "%.2f" % value

    guess = mixer.db_to_volume(-6.0)
    assert mixer.refine_db(NoDb(), guess, -6.0) == guess
    assert mixer.refine_db(None, guess, -6.0) == guess
    assert mixer.refine_db(fader, 0.0, float("-inf")) == 0.0


def test_parse_level_uses_the_parameter_display():
    fader = FakeFader(offset=-0.2)
    value = mixer.parse_level("-6 dB", 0.85, parameter=fader)
    assert float(fader.str_for_value(value).split()[0]) == pytest.approx(-6.0, abs=2e-3)
    send = FakeFader(offset=0.1, send=True, value=0.0)
    value = mixer.parse_level("-12 dB", 0.0, "send", send=True, parameter=send)
    assert float(send.str_for_value(value).split()[0]) == pytest.approx(-12.0, abs=2e-3)
    assert mixer.parse_level("-inf", 0.85, parameter=fader) == 0.0
    assert mixer.parse_level(0.5, 0.85, parameter=fader) == 0.5


def test_db_value_never_reports_negative_zero():
    assert str(mixer.db_value(-0.0001)) == "0.0"
    assert mixer.db_value(float("-inf")) == "-inf"
