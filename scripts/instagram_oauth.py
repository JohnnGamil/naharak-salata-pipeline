"""One-time Instagram OAuth (Instagram API with Instagram Login): prints the consent URL, takes the
redirected URL, stores a long-lived token in the DB.

    python -m scripts.instagram_oauth           # connect
    python -m scripts.instagram_oauth --check   # show the connected account
"""
from __future__ import annotations

import argparse
import secrets
import sys
import urllib.parse

from src.config import get_settings
from src.db import DB
from src.upload import r2
from src.upload.instagram_api import InstagramTokens, authorize_url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if not (s.instagram_app_id and s.instagram_app_secret and s.instagram_redirect_uri):
        print("Set INSTAGRAM_APP_ID, INSTAGRAM_APP_SECRET and INSTAGRAM_REDIRECT_URI in .env first.")
        return 1
    tokens = InstagramTokens(DB(s.db_path), s.instagram_app_id, s.instagram_app_secret, s.instagram_api_version)
    if not args.check:
        state = secrets.token_urlsafe(16)
        print("\n1) Open this URL, log in with the Instagram (Professional) account and allow:\n")
        print(authorize_url(s.instagram_app_id, s.instagram_redirect_uri, state))
        print("\n2) You'll land on your redirect URI. Copy the WHOLE address-bar URL and paste it here:")
        q = urllib.parse.parse_qs(urllib.parse.urlparse(input("> ").strip()).query)
        if q.get("state", [None])[0] != state or "code" not in q:
            print(f"no valid code in that URL: {q}")
            return 1
        tokens.exchange_code(q["code"][0], s.instagram_redirect_uri)
    tok = tokens.get() or {}
    print("instagram account:", tok.get("username"), tok.get("user_id"))
    print("R2 public links:", "ready" if r2.enabled(s) else "NOT set (R2_* in .env) - needed to post Reels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
