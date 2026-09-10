"""``Live.Device`` — the device base class, its view and ``DeviceType``
(undefined=0, instrument=1, audio_effect=2, midi_effect=4)."""

from ._model import Device, DeviceType, DeviceView  # noqa: F401

Device.View = DeviceView
