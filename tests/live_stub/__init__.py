"""The fake Live environment used by the LiveBridge test-suite.

``tests/conftest.py`` puts this directory on ``sys.path`` so ``import Live``,
``import _Framework`` and ``import ableton.v2.control_surface`` resolve to the
fakes, and puts ``tests/`` on ``sys.path`` so ``from live_stub import factory``
works.  See ``README.md`` next to this file.
"""
