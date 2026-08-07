"""Local Chrome persistent-profile backend (Windows Phase 1).

This package owns the lifecycle of a single visible Google Chrome process that
reuses a dedicated persistent profile, so a user can log in once and have all
subsequent checks reuse that login state. See
docs/superpowers/specs/2026-08-07-local-chrome-persistent-profile-design.md
"""
import sys

# Indirection so tests can monkeypatch the platform without touching sys.platform.
_PLATFORM = sys.platform


def is_local_chrome_supported() -> bool:
    """True only on Windows in Phase 1."""
    return _PLATFORM == 'win32'


def is_local_chrome_enabled(datastore) -> bool:
    """True when the feature is both supported and turned on in settings."""
    if not is_local_chrome_supported():
        return False
    try:
        return bool(datastore.data['settings']['requests']['local_chrome']['enabled'])
    except (KeyError, TypeError):
        return False
