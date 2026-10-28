"""Today's trend in Egypt for the standalone videos (John, 2026-10-28: "a joke on today's trend").

Google Trends' daily feed for Egypt (free, no key) -> Gemini keeps only a topic that is safe and fun
for a kids' produce channel (weather, a big match, exams, prices, a holiday, a harmless viral thing)
and turns it into an angle. Real people, scandals, accidents, deaths, crime, politics and religion
are never used. One pick per Cairo day, cached in the DB.
"""
from __future__ import annotations

import datetime as dt
import logging
import xml.etree.ElementTree as ET

import httpx

from ..llm import LLMUnavailable, extract_json

log = logging.getLogger(__name__)

FEED = "https://trends.google.com/trending/rss?geo=EG"
NS = {"ht": "https://trends.google.com/trending/rss"}

PICK = """These are today's top Google searches in Egypt (search term | a news headline about it):
{items}

We run a family-friendly Egyptian TikTok channel of cute talking fruits and vegetables (kids + adults).
Pick AT MOST ONE trend we can joke about today. It must be an everyday topic everyone shares: weather,
a big football match (talk about the match, not players' private lives), exams/results/school, prices,
a holiday or season, a food, a harmless funny viral thing, a new phone/app/game everyone talks about.
NEVER: a real person as the subject, a scandal, an accident, a death, illness, crime, a court case,
politics, war, religion, or anything sad. If nothing fits, use=false.
Return ONLY JSON: {{"use": true, "trend": "the search term", "topic_en": "the safe topic in English",
"angle": "one sentence: how our vegetables could react to it in a funny way"}}"""


def fetch(limit: int = 20) -> list[str]:
    r = httpx.get(FEED, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    out = []
    for item in ET.fromstring(r.content).iter("item"):
        title = (item.findtext("title") or "").strip()
        news = item.find("ht:news_item", NS)
        head = (news.findtext("ht:news_item_title", default="", namespaces=NS) if news is not None else "").strip()
        if title:
            out.append(f"{title} | {head[:120]}")
        if len(out) >= limit:
            break
    return out


def todays_trend(llm, db, today: dt.date | None = None) -> dict | None:
    """{"trend", "topic_en", "angle"} or None (nothing safe today, or the feed/LLM is down)."""
    key = f"trend:{(today or dt.date.today()).isoformat()}"
    cached = db.get(key)
    if cached is not None:
        return cached or None
    try:
        items = fetch()
        pick = extract_json(llm.text(PICK.format(items="\n".join(f"- {i}" for i in items)),
                                     json_mode=True, temperature=0.3)) if items else {}
    except (httpx.HTTPError, ET.ParseError, LLMUnavailable, ValueError) as e:
        log.warning("trend check skipped: %s", e)
        return None  # not cached: try again next run
    got = pick if isinstance(pick, dict) and pick.get("use") and pick.get("topic_en") else {}
    db.put(key, got)
    log.info("today's trend: %s", got or "none safe")
    return got or None
