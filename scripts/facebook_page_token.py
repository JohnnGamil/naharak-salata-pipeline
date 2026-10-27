"""Store a never-expiring Facebook Page token.

    python -m scripts.facebook_page_token
then paste the whole address of the callback page (it carries long_lived_token after the
Facebook login), or a long-lived user token. The Page token made from a long-lived user token
does not expire. Nothing secret is printed.
"""
from __future__ import annotations

import sys
import urllib.parse

import httpx

from src.config import get_settings
from src.db import DB
from src.upload.facebook_page import GRAPH, TOKEN_KEY

PAGE_ID = "1431213030065767"  # نهارك سلطة


def main() -> int:
    s = get_settings()
    raw = (sys.argv[1] if len(sys.argv) > 1 else input("Paste the callback address (or token): ")).strip()
    if "#" in raw or "?" in raw:
        frag = urllib.parse.parse_qs(urllib.parse.urlparse(raw).fragment)
        user = (frag.get("long_lived_token") or frag.get("access_token") or [""])[0]
    else:
        user = raw
    if not user:
        print("no token found in what you pasted")
        return 1
    page = httpx.get(f"{GRAPH}/{PAGE_ID}", timeout=30,
                     params={"fields": "id,name,access_token", "access_token": user}).json()
    if "access_token" not in page:
        print("Facebook refused:", page.get("error", page))
        return 1
    dbg = httpx.get(f"{GRAPH}/debug_token", timeout=30,
                    params={"input_token": page["access_token"], "access_token": page["access_token"]}).json()
    exp = (dbg.get("data") or {}).get("expires_at")
    DB(s.db_path).put(TOKEN_KEY, {"page_id": page["id"], "page_token": page["access_token"], "name": page["name"]})
    print(f"saved Page {page['name']} ({page['id']}); expires: {'never' if exp == 0 else exp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
