"""
BLK MRKT — Email utility.

Supports Resend, SendGrid, and Mailgun via their REST APIs (stdlib urllib only).
Set EMAIL_PROVIDER, EMAIL_API_KEY, EMAIL_FROM in environment.
Falls back silently to a console log when email is not configured.
"""

import json
import urllib.request
import urllib.parse

from config import EMAIL_PROVIDER, EMAIL_API_KEY, EMAIL_FROM, EMAIL_ENABLED


def send_email(to: str, subject: str, text: str, html: str = "") -> bool:
    """
    Send a transactional email.

    Returns True on success, False on failure.
    If EMAIL_ENABLED is False, logs to stdout and returns True (dev mode).
    """
    if not EMAIL_ENABLED:
        print(f"[EMAIL — dev mode, not sent]\nTo: {to}\nSubject: {subject}\n{text}\n")
        return True

    provider = EMAIL_PROVIDER.lower()

    if provider == "resend":
        return _send_resend(to, subject, text, html)
    elif provider == "sendgrid":
        return _send_sendgrid(to, subject, text, html)
    elif provider == "mailgun":
        return _send_mailgun(to, subject, text, html)
    else:
        print(f"[EMAIL] Unknown provider '{EMAIL_PROVIDER}' — not sent.")
        return False


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _http_post(url, headers, body_bytes):
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300, resp.read()
    except urllib.error.HTTPError as e:
        print(f"[EMAIL] HTTP error {e.code}: {e.read()}")
        return False, None
    except Exception as e:
        print(f"[EMAIL] Error: {e}")
        return False, None


def _send_resend(to, subject, text, html):
    payload = {
        "from": EMAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if html:
        payload["html"] = html

    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {EMAIL_API_KEY}",
        "Content-Type": "application/json",
    }
    ok, _ = _http_post("https://api.resend.com/emails", headers, body)
    return ok


def _send_sendgrid(to, subject, text, html):
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": EMAIL_FROM},
        "subject": subject,
        "content": [{"type": "text/plain", "value": text}],
    }
    if html:
        payload["content"].append({"type": "text/html", "value": html})

    body = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {EMAIL_API_KEY}",
        "Content-Type": "application/json",
    }
    ok, _ = _http_post("https://api.sendgrid.com/v3/mail/send", headers, body)
    return ok


def _send_mailgun(to, subject, text, html):
    # Mailgun domain is embedded in the API key as convention: key@domain
    # Or set EMAIL_FROM domain. We derive it from EMAIL_FROM.
    domain = EMAIL_FROM.split("@")[-1] if "@" in EMAIL_FROM else "mg.blkmrkt.com"
    url = f"https://api.mailgun.net/v3/{domain}/messages"

    data = {
        "from": EMAIL_FROM,
        "to": to,
        "subject": subject,
        "text": text,
    }
    if html:
        data["html"] = html

    body = urllib.parse.urlencode(data).encode()
    import base64
    credentials = base64.b64encode(f"api:{EMAIL_API_KEY}".encode()).decode()
    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    ok, _ = _http_post(url, headers, body)
    return ok
