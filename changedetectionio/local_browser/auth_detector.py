"""Generic login / security-challenge detector (spec 10).

Multi-signal scoring:
  - One strong signal  -> ATTENTION_REQUIRED
  - Two+ independent weak signals -> ATTENTION_REQUIRED
  - A single weak signal alone never triggers a pause.

Logs only trigger reasons - never cookies, auth headers, or form values (spec 10/14).
"""
import re

# URL path fragments that indicate an auth flow.
_STRONG_URL_PATTERNS = re.compile(
    r'(?:/login|/signin|/sign-in|/auth|/account/login|/passport|/sso|/oauth)',
    re.IGNORECASE,
)

# Visible password / captcha / verification components in the HTML.
_STRONG_HTML_PATTERNS = [
    re.compile(r'<input[^>]+type=["\']password["\']', re.IGNORECASE),
    re.compile(r'class=["\'][^"\']*(?:captcha|geetest|nc_iconfont|slider|slide-to-verify|qr[_-]?code|verify)[^"\']*', re.IGNORECASE),
    re.compile(r'id=["\'][^"\']*(?:captcha|geetest|slider|qr[_-]?login|verify)[^"\']*', re.IGNORECASE),
]


def _strong_signals(url, status_code, page_html):
    reasons = []
    if _STRONG_URL_PATTERNS.search(url or ''):
        reasons.append("URL entered an authentication path (login/signin/auth)")
    if status_code in (401, 403):
        reasons.append(f"HTTP status {status_code} indicates authentication is required")
    for pat in _STRONG_HTML_PATTERNS:
        m = pat.search(page_html or '')
        if m:
            reasons.append(f"Visible authentication component present ({m.group(0)[:60]})")
    return reasons


# Weak-signal text fragments (Chinese + English). A single match alone is NOT enough.
_WEAK_TEXT_PATTERNS = re.compile(
    r'(请登录|请先登录|登录后查看|验证身份|安全验证|身份验证|滑块验证|短信验证|'
    r'please\s+log\s*in|sign\s*in\s+to\s+continue|verify\s+your\s+identity|'
    r'authentication\s+required|are\s+you\s+a\s+robot)',
    re.IGNORECASE,
)

# Weak-signal title fragments (independent from body text).
_WEAK_TITLE_PATTERNS = re.compile(
    r'(登录|验证|安全|login|verify|authentication|access\s+denied)',
    re.IGNORECASE,
)


def _weak_signals(url, status_code, page_title, page_text, page_html) -> list:
    reasons = []
    if _WEAK_TEXT_PATTERNS.search(page_text or ''):
        reasons.append("Page text contains a common login/verification phrase")
    if _WEAK_TITLE_PATTERNS.search(page_title or ''):
        reasons.append("Page title suggests an authentication/verification page")
    # Reasons only ever describe the *category* of signal, never page content,
    # so cookies / tokens / form values can never leak (spec 10/14).
    return reasons


def detect_auth_challenge(*, url, status_code, page_title, page_text, page_html) -> dict:
    strong = _strong_signals(url, status_code, page_html)
    if strong:
        return {'required': True, 'reasons': strong}

    weak = _weak_signals(url, status_code, page_title, page_text, page_html)
    if len(weak) >= 2:
        return {'required': True, 'reasons': weak}

    return {'required': False, 'reasons': []}
