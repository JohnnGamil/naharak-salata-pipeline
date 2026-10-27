"""One-time YouTube OAuth (Google "Desktop app" client): opens the consent page, catches the code on
a loopback port, stores the tokens in the DB.

    python -m scripts.youtube_oauth           # connect
    python -m scripts.youtube_oauth --check   # show the connected channel
"""
from __future__ import annotations

import argparse
import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

from src.config import get_settings
from src.db import DB
from src.upload.youtube_api import YouTubeTokens, YouTubeUploader, authorize_url


def catch_code(redirect_uri: str, state: str, timeout: int = 300) -> str | None:
    u = urllib.parse.urlparse(redirect_uri)
    got: dict = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [None])[0] == state and "code" in q:
                got["code"] = q["code"][0]
                msg = "YouTube connected - you can close this tab."
            else:
                msg = f"Something went wrong: {q.get('error', ['no code'])[0]}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer((u.hostname, u.port or 80), H)
    srv.timeout = timeout
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    t.join(timeout)
    srv.server_close()
    return got.get("code")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if not (s.youtube_client_id and s.youtube_client_secret):
        print("Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in .env first.")
        return 1
    tokens = YouTubeTokens(DB(s.db_path), s.youtube_client_id, s.youtube_client_secret)
    if not args.check:
        state = secrets.token_urlsafe(16)
        url = authorize_url(s.youtube_client_id, s.youtube_redirect_uri, state)
        print("\nOpening the Google consent page. Pick the channel's Google account and allow access.\n")
        print(url, "\n")
        webbrowser.open(url)
        code = catch_code(s.youtube_redirect_uri, state)
        if not code:
            print("No code received (timed out). Run it again.")
            return 1
        tokens.exchange_code(code, s.youtube_redirect_uri)
        print("Saved YouTube tokens.")
    ch = YouTubeUploader(tokens).channel()
    print("channel:", (ch.get("snippet") or {}).get("title"), ch.get("id"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
