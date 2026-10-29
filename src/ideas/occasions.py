"""Egypt's calendar of occasions for the standalone videos (John, 2026-10-29: "like the winter-time
video, but automatic").

A day or two before an occasion, the next standalone video is about it: Ramadan, the Eids, Sham
el-Nessim, the clock changes, Mother's Day, the start of winter, Black Friday... Gregorian ones are
computed; the Islamic ones are the expected dates (the real day follows the moon sighting and can move
by a day), so those videos only say the occasion is "coming", never a date. Each occasion is used once a
year. Politics and religion-as-debate stay out: the Islamic and Coptic feasts are celebrations only.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

LEAD_DAYS = 2  # a standalone written up to 2 days before (production runs ahead of the post time)

# expected first day (Egypt) - the moon sighting can move it a day
ISLAMIC = {
    "ramadan": ["2027-02-08", "2028-01-28", "2029-01-16", "2030-01-05", "2030-12-26", "2031-12-15"],
    "eid_fitr": ["2027-03-10", "2028-02-26", "2029-02-14", "2030-02-04", "2031-01-24", "2032-01-14"],
    "eid_adha": ["2027-05-16", "2028-05-05", "2029-04-24", "2030-04-13", "2031-04-02", "2032-03-22"],
    "hijri_new_year": ["2027-06-06", "2028-05-25", "2029-05-14", "2030-05-03", "2031-04-23", "2032-04-11"],
}

ABOUT = {
    "ramadan": "Ramadan is about to start: the first suhoor, the fanoos, the Ramadan decorations in the street, "
               "who will cook the first iftar, the vegetables preparing for the month (never mock fasting or prayer)",
    "eid_fitr": "Eid al-Fitr is coming: kahk and biscuits, new clothes, the Eid money (العيدية), visiting family",
    "eid_adha": "Eid al-Adha is coming: the big family lunch, the fatta, visiting family, the holiday trip "
                "(nothing about slaughter or blood)",
    "hijri_new_year": "the Hijri new year holiday: a day off, new-year wishes, plans for the long weekend",
    "coptic_christmas": "Coptic Christmas on 7 January: wishing our Christian neighbours a happy feast, the big "
                        "family dinner, a warm Egyptian 'كل سنة وانتم طيبين' (a celebration, never a debate)",
    "new_year": "New Year's Eve: resolutions nobody keeps, the countdown, who stays awake until midnight",
    "valentine": "Valentine's Day (عيد الحب): red roses, teddy bears, the single vegetables complaining, kept "
                 "sweet and family-friendly",
    "mothers_day": "Mother's Day on 21 March: what gift to buy mom, the kids saving pocket money, a touching "
                   "funny thank-you to every Egyptian mom",
    "sham_el_nessim": "Sham el-Nessim: coloured eggs, feseekh and renga (the onions and green onions are the "
                      "stars), the picnic in the park and on the Nile",
    "summer_time": "Egypt switches to SUMMER time tonight: at midnight the clocks go FORWARD one hour "
                   "(12:00 becomes 1:00), so we lose one hour of sleep; the phone changes by itself, the wall clock does not",
    "winter_time": "Egypt switches to WINTER time tonight: at midnight the clocks go BACK one hour "
                   "(12:00 becomes 11:00), one extra hour of sleep; the phone changes by itself, the wall clock does not",
    "summer_start": "the first day of summer: the heat, the beach plans, the AC war at home, watermelon season",
    "winter_start": "the first day of winter: the cold, blankets, hot drinks, nobody wants to get out of bed",
    "school_start": "the new school year is about to start: new bags, school uniforms, the first-day panic",
    "black_friday": "Black Friday / White Friday sales: everybody hunting discounts, the vegetables shopping online",
}


def _orthodox_easter(year: int) -> dt.date:
    a, b, c = year % 4, year % 7, year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month, day = divmod(d + e + 114, 31)
    julian = dt.date(year, month, day + 1)
    return julian + dt.timedelta(days=13)  # Julian -> Gregorian (1900-2099)


def _clock_change(year: int, tz: ZoneInfo, forward: bool) -> dt.date | None:
    """The day whose night the clocks move (offset differs between that noon and the next noon)."""
    day = dt.date(year, 3, 1) if forward else dt.date(year, 9, 1)
    for _ in range(120):
        a = dt.datetime.combine(day, dt.time(12), tzinfo=tz).utcoffset()
        b = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(12), tzinfo=tz).utcoffset()
        if a != b and (b > a) == forward:
            return day
        day += dt.timedelta(days=1)
    return None


def calendar(year: int, tz: ZoneInfo) -> list[tuple[dt.date, str]]:
    out = [(dt.date(year, 1, 1), "new_year"), (dt.date(year, 1, 7), "coptic_christmas"),
           (dt.date(year, 2, 14), "valentine"), (dt.date(year, 3, 21), "mothers_day"),
           (_orthodox_easter(year) + dt.timedelta(days=1), "sham_el_nessim"),
           (dt.date(year, 6, 21), "summer_start"), (dt.date(year, 9, 20), "school_start"),
           (dt.date(year, 12, 21), "winter_start")]
    nov_last_friday = max(dt.date(year, 11, d) for d in range(1, 31) if dt.date(year, 11, d).weekday() == 4)
    out.append((nov_last_friday, "black_friday"))
    for key, forward in (("summer_time", True), ("winter_time", False)):
        d = _clock_change(year, tz, forward)
        if d:
            out.append((d, key))
    for key, days in ISLAMIC.items():
        out += [(dt.date.fromisoformat(d), key) for d in days if d.startswith(str(year))]
    return sorted(out)


def coming(today: dt.date, tz: ZoneInfo, lead: int = LEAD_DAYS) -> list[tuple[dt.date, str]]:
    """Occasions from today to `lead` days ahead, nearest first."""
    rows = calendar(today.year, tz) + calendar(today.year + 1, tz)
    return [(d, k) for d, k in rows if 0 <= (d - today).days <= lead]


def premise_for(db, tz: ZoneInfo, today: dt.date | None = None) -> str:
    """Premise text for the next standalone when an occasion is near and not used yet (marks it used)."""
    today = today or dt.datetime.now(tz).date()
    for day, key in coming(today, tz):
        mark = f"occasion:{key}:{day.isoformat()}"
        if db.get(mark):
            continue
        db.put(mark, today.isoformat())
        when = "TODAY" if day == today else ("TOMORROW" if (day - today).days == 1 else "in two days")
        return (f"USE THIS PREMISE (a special episode for an Egyptian occasion that is {when}; the video posts "
                f"before it and everyone in Egypt is talking about it): {ABOUT[key]}. Our vegetables live it in "
                "their own funny way, with at least one line every Egyptian will recognise from their own home. "
                "State no facts beyond the ones written here, and no dates unless written here.")
    return ""
