"""Settings parsing — the whitelist and the two time windows."""
from datetime import timedelta

import pytest

from tests.conftest import make_settings


def test_allowed_chat_id_set_splits_and_strips():
    s = make_settings(allowed_chat_ids="Cabc, Cdef ,Cghi")
    assert s.allowed_chat_id_set == {"Cabc", "Cdef", "Cghi"}


def test_allowed_chat_id_set_drops_empty_entries():
    s = make_settings(allowed_chat_ids="Cabc,,  ,Cdef,")
    assert s.allowed_chat_id_set == {"Cabc", "Cdef"}


def test_allowed_chat_id_set_empty_means_no_whitelist():
    # An empty set is the documented "allow every chat" mode (app.py guards on
    # truthiness), so it must not become {""}.
    assert make_settings().allowed_chat_id_set == set()


def test_context_and_session_windows_are_timedeltas():
    s = make_settings(context_window_hours=6, session_window_minutes=3)
    assert s.context_window == timedelta(hours=6)
    assert s.session_window == timedelta(minutes=3)


def test_defaults_match_documented_behaviour():
    s = make_settings()
    assert s.context_window == timedelta(hours=12)
    assert s.session_window == timedelta(minutes=10)
    assert s.use_firestore is False
    assert s.auto_describe_images is False


def test_required_credentials_are_mandatory():
    from family_line_bot.config import Settings

    with pytest.raises(Exception):
        Settings(_env_file=None, line_channel_secret="x")
