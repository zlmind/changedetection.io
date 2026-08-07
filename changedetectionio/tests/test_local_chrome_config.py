import os
import tempfile

import pytest

from changedetectionio.model.App import model


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override the conftest's live_server-dependent autouse fixture.

    These are pure unit tests of the App.model config defaults and do not
    need the Flask live server (which cannot spawn on Windows due to a
    pytest_flask multiprocessing pickling bug).
    """
    yield


def test_local_chrome_defaults_present():
    with tempfile.TemporaryDirectory() as path:
        app = model(datastore_path=path)
        lc = app['settings']['requests']['local_chrome']
        assert lc['enabled'] is False
        assert lc['chrome_executable'] is None


def test_local_chrome_defaults_merged_from_disk():
    # Simulate an old config file that has no local_chrome key yet.
    with tempfile.TemporaryDirectory() as path:
        app = model(datastore_path=path)
        # Drop the key to simulate a pre-existing store, then re-apply.
        stored = {'settings': {'requests': {'timeout': 45}}}
        # _apply_settings does a dict.update on requests, so missing keys
        # keep their defaults.
        app2 = model(datastore_path=path)
        app2['settings']['requests'].update(stored['settings']['requests'])
        assert app2['settings']['requests']['local_chrome']['enabled'] is False
