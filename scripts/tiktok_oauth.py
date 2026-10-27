"""One-time TikTok OAuth: prints the consent URL, takes the redirected URL, stores tokens in the DB.

Usage (on the host, or inside the container):
    python -m scripts.tiktok_oauth            # web app (https redirect URI)
    python -m scripts.tiktok_oauth --pkce     # desktop app flow
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse

from src.config import get_settings
from src.db import DB
from src.upload.tiktok_api import TikTokUploader, TokenStore, authorize_url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkce", action="store_true", help="use the desktop PKCE flow")
    ap.add_argument("--check", action="store_true", help="only test the stored token")
    args = ap.parse_args()
    s = get_settings()
    if not (s.tiktok_client_key and s.tiktok_client_secret and s.tiktok_redirect_uri):
        print("Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI in .env first.")
        return 1
    store = TokenStore(DB(s.db_path), s.tiktok_client_key, s.tiktok_client_secret)
    if not args.check:
        url, state, verifier = authorize_url(s.tiktok_client_key, s.tiktok_redirect_uri, args.pkce)
        print("\n1) Open this URL, log in with the TikTok account and approve:\n")
        print(url)
        print("\n2) You'll land on your redirect URI. Copy the WHOLE address-bar URL and paste it here:")
        redirected = input("> ").strip()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(redirected).query)
        if q.get("state", [None])[0] != state:
            print("state mismatch - start again")
            return 1
        if "code" not in q:
            print(f"no code in URL: {q}")
            return 1
        tok = store.exchange_code(q["code"][0], s.tiktok_redirect_uri, verifier)
        print(f"Saved tokens (scope: {tok.get('scope')}).")
    info = TikTokUploader(store).creator_info()
    print("creator_info:", {k: info.get(k) for k in ("creator_nickname", "privacy_level_options",
                                                     "max_video_post_duration_sec")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
