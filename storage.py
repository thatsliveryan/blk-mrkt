"""
BLK MRKT — Cloudflare R2 Object Storage

AWS S3-compatible API via stdlib only (no boto3).
Implements AWS Signature Version 4 for request signing.

Dual-write strategy:
  - Upload: write to local filesystem first, then async-ish to R2.
  - Serve:  serve from local if file exists; fall back to R2 signed URL.
  - Delete: remove from both.

Feature flag: R2_ENABLED (auto-set when R2_ACCOUNT_ID + keys are present).
When R2_ENABLED is False the module is a no-op and local storage is used.
"""

import hashlib
import hmac
import datetime
import urllib.request
import urllib.parse
import urllib.error
import os

from config import (
    R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY,
    R2_BUCKET, R2_ENABLED,
    AUDIO_DIR, COVERS_DIR,
)

# ---------------------------------------------------------------------------
# R2 endpoint
# ---------------------------------------------------------------------------

R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
R2_REGION   = "auto"
SERVICE     = "s3"


# ---------------------------------------------------------------------------
# AWS Signature V4 helpers
# ---------------------------------------------------------------------------

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signing_key(date_str: str) -> bytes:
    k_date    = _sign(("AWS4" + R2_SECRET_KEY).encode(), date_str)
    k_region  = _sign(k_date, R2_REGION)
    k_service = _sign(k_region, SERVICE)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_headers(method: str, key: str, body: bytes = b"",
                  content_type: str = "application/octet-stream",
                  extra_headers: dict = None) -> dict:
    """
    Build Authorization + x-amz-* headers for an R2 request.
    """
    now   = datetime.datetime.utcnow()
    amzdate  = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    host = f"{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    canonical_uri = f"/{R2_BUCKET}/{urllib.parse.quote(key, safe='/')}"

    payload_hash = _sha256_hex(body)

    headers_map = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amzdate,
    }
    if body:
        headers_map["content-type"] = content_type
        headers_map["content-length"] = str(len(body))

    if extra_headers:
        for k, v in extra_headers.items():
            headers_map[k.lower()] = v

    signed_header_keys = sorted(headers_map.keys())
    canonical_headers  = "".join(f"{k}:{headers_map[k]}\n" for k in signed_header_keys)
    signed_headers_str = ";".join(signed_header_keys)

    canonical_request = "\n".join([
        method,
        canonical_uri,
        "",                      # query string (empty)
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])

    credential_scope = f"{datestamp}/{R2_REGION}/{SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amzdate,
        credential_scope,
        _sha256_hex(canonical_request.encode()),
    ])

    signing_key = _get_signing_key(datestamp)
    signature   = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    auth_header = (
        f"AWS4-HMAC-SHA256 Credential={R2_ACCESS_KEY}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )

    result = dict(headers_map)
    result["authorization"] = auth_header
    # urllib needs title-case keys
    return {k.title().replace("X-Amz", "X-Amz"): v for k, v in result.items()}


def _r2_url(key: str) -> str:
    return f"{R2_ENDPOINT}/{R2_BUCKET}/{urllib.parse.quote(key, safe='/')}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upload(key: str, data: bytes, content_type: str = "application/octet-stream") -> tuple:
    """
    Upload bytes to R2.
    Returns (True, None) on success, (False, error_str) on failure.
    No-op returning (True, None) if R2_ENABLED is False.
    """
    if not R2_ENABLED:
        return True, None

    url     = _r2_url(key)
    headers = _make_headers("PUT", key, data, content_type)
    req     = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status < 300, None
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")
        return False, f"R2 PUT {e.code}: {msg}"
    except Exception as e:
        return False, str(e)


def download(key: str) -> tuple:
    """
    Download bytes from R2.
    Returns (data_bytes, content_type) on success, (None, None) on failure.
    """
    if not R2_ENABLED:
        return None, None

    url     = _r2_url(key)
    headers = _make_headers("GET", key)
    req     = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            ct = resp.headers.get("Content-Type", "application/octet-stream")
            return resp.read(), ct
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, None
        print(f"[R2] GET error {e.code}: {e.read()}")
        return None, None
    except Exception as e:
        print(f"[R2] download error: {e}")
        return None, None


def delete(key: str) -> bool:
    """Delete an object from R2. Returns True on success."""
    if not R2_ENABLED:
        return True

    url     = _r2_url(key)
    headers = _make_headers("DELETE", key)
    req     = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True  # Already gone
        print(f"[R2] DELETE error {e.code}")
        return False
    except Exception as e:
        print(f"[R2] delete error: {e}")
        return False


def exists(key: str) -> bool:
    """HEAD request to check if an object exists in R2."""
    if not R2_ENABLED:
        return False

    url     = _r2_url(key)
    headers = _make_headers("HEAD", key)
    req     = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as e:
        return e.code != 404
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Dual-write helpers used by drops.py
# ---------------------------------------------------------------------------

def save_audio(local_path: str, r2_key: str) -> tuple:
    """
    Read a file from local_path and upload to R2.
    Returns (True, None) or (False, error_str).
    """
    if not R2_ENABLED:
        return True, None
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(local_path)[1].lower()
        ct  = "audio/mpeg" if ext == ".mp3" else "audio/wav"
        return upload(r2_key, data, ct)
    except Exception as e:
        return False, str(e)


def save_cover(local_path: str, r2_key: str) -> tuple:
    """
    Read a cover image from local_path and upload to R2.
    Returns (True, None) or (False, error_str).
    """
    if not R2_ENABLED:
        return True, None
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(local_path)[1].lower()
        ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png", ".webp": "image/webp"}
        ct = ct_map.get(ext, "image/jpeg")
        return upload(r2_key, data, ct)
    except Exception as e:
        return False, str(e)


def fetch_audio(r2_key: str, local_fallback_path: str = None) -> tuple:
    """
    Fetch audio bytes. Tries R2 first, falls back to local file.
    Returns (data_bytes, content_type) or (None, None).
    """
    if R2_ENABLED and r2_key:
        data, ct = download(r2_key)
        if data:
            return data, ct

    # Local fallback
    if local_fallback_path and os.path.exists(local_fallback_path):
        with open(local_fallback_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(local_fallback_path)[1].lower()
        ct  = "audio/mpeg" if ext == ".mp3" else "audio/wav"
        return data, ct

    return None, None


def fetch_cover(r2_key: str, local_fallback_path: str = None) -> tuple:
    """
    Fetch cover image bytes. Tries R2 first, falls back to local file.
    Returns (data_bytes, content_type) or (None, None).
    """
    if R2_ENABLED and r2_key:
        data, ct = download(r2_key)
        if data:
            return data, ct

    if local_fallback_path and os.path.exists(local_fallback_path):
        with open(local_fallback_path, "rb") as f:
            data = f.read()
        ext = os.path.splitext(local_fallback_path)[1].lower()
        ct_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png", ".webp": "image/webp"}
        ct = ct_map.get(ext, "image/jpeg")
        return data, ct

    return None, None
