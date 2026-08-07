import pytest

from changedetectionio.local_browser.auth_detector import detect_auth_challenge


@pytest.fixture(autouse=True)
def prepare_test_function():
    """Override conftest's live_server-dependent autouse fixture for pure unit tests."""
    yield


def test_login_url_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/login", status_code=200,
                              page_title="Login", page_text="welcome", page_html="<html></html>")
    assert r['required'] is True
    assert any('login' in reason.lower() or 'url' in reason.lower() for reason in r['reasons'])


def test_401_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/page", status_code=401,
                              page_title="", page_text="", page_html="")
    assert r['required'] is True
    assert any('401' in reason for reason in r['reasons'])


def test_403_is_strong_signal():
    r = detect_auth_challenge(url="https://example.com/page", status_code=403,
                              page_title="", page_text="", page_html="")
    assert r['required'] is True


def test_visible_password_field_is_strong_signal():
    html = '<input type="password" name="pwd" />'
    r = detect_auth_challenge(url="https://example.com/page", status_code=200,
                              page_title="Sign in", page_text="sign in", page_html=html)
    assert r['required'] is True
    assert any('password' in reason.lower() for reason in r['reasons'])


def test_captcha_components_are_strong_signal():
    html = '<div class="geetest_captcha">slide</div>'
    r = detect_auth_challenge(url="https://example.com/page", status_code=200,
                              page_title="verify", page_text="slide to verify", page_html=html)
    assert r['required'] is True
