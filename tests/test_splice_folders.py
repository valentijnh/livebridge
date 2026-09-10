"""Splice download-folder resolution as checked on the real Mac (T4): the Splice app keeps its
folder in ~/Splice with "sounds" and "presets" sub-folders; on Windows the default is
%USERPROFILE%\\Splice (older installs: Documents\\Splice)."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
for _path in (str(_TESTS.parent / "mcp_server"), str(_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from livebridge_mcp.tools import splice as splice_tools  # noqa: E402

from LiveBridge.handlers import samples as samples_handlers  # noqa: E402


def test_macos_layout_prefers_the_sounds_folder(tmp_path):
    home = tmp_path / "home"
    (home / "Splice" / "sounds").mkdir(parents=True)
    (home / "Splice" / "presets").mkdir()
    candidates = samples_handlers.splice_candidates(environ={"HOME": str(home)},
                                                    windows=False)
    assert candidates[:2] == [str(home / "Splice" / "sounds"), str(home / "Splice")]
    existing = [c for c in candidates if Path(c).is_dir()]
    assert existing[0] == str(home / "Splice" / "sounds")


def test_windows_default_comes_from_userprofile():
    env = {"USERPROFILE": "C:\\Users\\Valentijn", "HOME": "/ignored"}
    candidates = samples_handlers.splice_candidates(environ=env, windows=True)
    assert candidates[:2] == ["C:\\Users\\Valentijn\\Splice\\sounds",
                              "C:\\Users\\Valentijn\\Splice"]
    assert "C:\\Users\\Valentijn\\Documents\\Splice" in candidates


def test_custom_folder_from_the_environment_wins():
    env = {"USERPROFILE": "C:\\Users\\Valentijn", "LIVEBRIDGE_SPLICE_DIR": "D:\\Samples\\Splice"}
    candidates = samples_handlers.splice_candidates(environ=env, windows=True)
    assert candidates[0] == "D:\\Samples\\Splice"


def test_setup_info_documents_folders_and_presets():
    folder = splice_tools.SETUP_INFO["download_folder"]
    assert "~/Splice" in folder["macos"]
    assert "%USERPROFILE%\\Splice" in folder["windows"]
    assert "presets" in folder["presets"] and "Serum" in folder["presets"]


def test_windows_onedrive_documents_are_tried():
    """OneDrive's "PC folder backup" moves Documents (and with it Splice's older folder and
    Ableton's User Library / Factory Packs) to %OneDrive%\\Documents."""
    env = {"USERPROFILE": "C:\\Users\\V", "OneDrive": "C:\\Users\\V\\OneDrive - Blub"}
    candidates = samples_handlers.splice_candidates(environ=env, windows=True)
    assert candidates[:2] == ["C:\\Users\\V\\Splice\\sounds", "C:\\Users\\V\\Splice"]
    assert candidates.index("C:\\Users\\V\\Documents\\Splice") < \
        candidates.index("C:\\Users\\V\\OneDrive - Blub\\Documents\\Splice")
    assert "C:\\Users\\V\\OneDrive\\Documents\\Splice\\sounds" in candidates
    assert len(candidates) == len(set(candidates))
    library = samples_handlers.user_library_candidates(environ=env, windows=True)
    assert library[0] == "C:\\Users\\V\\Documents\\Ableton\\User Library"
    assert "C:\\Users\\V\\OneDrive - Blub\\Documents\\Ableton\\User Library" in library
    packs = samples_handlers.factory_packs_candidates(environ=env, windows=True)
    assert packs[1] == "C:\\Users\\V\\OneDrive - Blub\\Documents\\Ableton\\Factory Packs"
