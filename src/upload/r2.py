"""Cloudflare R2 (S3-compatible, free 10 GB): gives a video a temporary public URL.

Instagram's Graph API does not take file uploads; it downloads the video from a public URL. We
PUT the mp4 into an R2 bucket that has public access (r2.dev subdomain or a custom domain) and
delete it once Instagram has published. AWS Signature V4 is done by hand to avoid boto3.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import urllib.parse
from pathlib import Path

import httpx

from .base import UploadError


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def _headers(s, method: str, path: str, payload_hash: str, extra: dict | None = None) -> tuple[str, dict]:
    host = f"{s.r2_account_id}.r2.cloudflarestorage.com"
    now = dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date, **(extra or {})}
    names = sorted(headers)
    canonical = "\n".join([method, urllib.parse.quote(path), "",
                           "".join(f"{k}:{headers[k]}\n" for k in names), ";".join(names), payload_hash])
    scope = f"{day}/auto/s3/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(f"AWS4{s.r2_secret_key}".encode(), day), "auto"), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (f"AWS4-HMAC-SHA256 Credential={s.r2_access_key}/{scope}, "
                                f"SignedHeaders={';'.join(names)}, Signature={sig}")
    return f"https://{host}{urllib.parse.quote(path)}", headers


def enabled(s) -> bool:
    return bool(s.r2_account_id and s.r2_access_key and s.r2_secret_key and s.r2_bucket and s.r2_public_base)


def put_public(s, path: Path, key: str, content_type: str = "video/mp4") -> str:
    """Upload a file, return its public URL."""
    data = path.read_bytes()
    url, headers = _headers(s, "PUT", f"/{s.r2_bucket}/{key}", hashlib.sha256(data).hexdigest(),
                            {"content-type": content_type})
    r = httpx.put(url, content=data, headers=headers, timeout=300)
    if r.status_code not in (200, 201):
        raise UploadError(f"R2 upload {r.status_code}: {r.text[:300]}", retryable=r.status_code >= 500)
    return f"{s.r2_public_base.rstrip('/')}/{urllib.parse.quote(key)}"


def delete(s, key: str) -> None:
    url, headers = _headers(s, "DELETE", f"/{s.r2_bucket}/{key}", hashlib.sha256(b"").hexdigest())
    try:
        httpx.delete(url, headers=headers, timeout=60)
    except httpx.HTTPError:
        pass  # a leftover file only costs storage; the bucket can also carry a 1-day lifecycle rule


def presign(s, method: str, key: str, expires: int = 900) -> str:
    """Query-string signed URL (AWS SigV4) for one object; lets a browser PUT/GET without the keys."""
    host = f"{s.r2_account_id}.r2.cloudflarestorage.com"
    now = dt.datetime.now(dt.timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{day}/auto/s3/aws4_request"
    path = urllib.parse.quote(f"/{s.r2_bucket}/{key}")
    q = {"X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{s.r2_access_key}/{scope}",
         "X-Amz-Date": amz_date, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    qs = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(q.items()))
    canonical = "\n".join([method, path, qs, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(f"AWS4{s.r2_secret_key}".encode(), day), "auto"), "s3"), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"https://{host}{path}?{qs}&X-Amz-Signature={sig}"
