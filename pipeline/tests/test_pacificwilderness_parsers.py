"""Unit tests for the Pacific Wilderness scraper's parsing helpers.

Network-free. The post text is the shop's own (Instagram captions from
Aug-Sep 2026 and the Facebook post of 2026-09-25), as its WordPress site
rendered them on 2026-10-15.

Run:
    python -m pytest pipeline/tests/test_pacificwilderness_parsers.py -v
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from validation.ingest import pacificwilderness as pw  # noqa: E402

# Instagram, 2026-09-04 18:00:30 UTC (11:00 PDT, a Friday).
IG_0904 = """LOCAL DIVING CONDITIONS UPDATE 🌊

🌤️ Coming Week’s Forecast
• Sunny skies, high 70s
• High swells expected, especially in Laguna

🤿 Recent Reports
• Oil Rigs: 60+ ft visibility | 63°F at 60 ft
• Point Fermin: 50 ft visibility | 72°F
• Catalina Island: 50+ ft visibility reported

📅 Upcoming Dives
• 9/7 – Island Diver, Palos Verdes — WAIT LIST
• 9/13 – Giant Stride, Oil Rigs — WAIT LIST"""
IG_0904_AT = datetime.fromtimestamp(1788544830, timezone.utc)

# Instagram, 2026-08-21 (Friday).
IG_0821 = """✅ Recent Reports
• Malaga Cove (PV): 20–30 ft visibility today
• Point Fermin (PV): 40 ft visibility last Monday
• Veteran’s Park: 15–25 ft visibility throughout the week

☀️ Weekend Forecast
• Calm surf & swells"""
IG_0821_AT = datetime.fromtimestamp(1787350676, timezone.utc)

# Instagram, 2026-08-28 (Friday).
IG_0828 = """LOCAL DIVING CONDITIONS

Diving conditions along the local coast have been varied this week.

Last Sunday, we completed 3 dives at the oil rigs aboard the Giant Stride with 50+ ft visibility and no surge or swells! Water temps were 64°F at 100 ft to 68°F at the surface.

Laguna has been less favorable, with 5–10 ft visibility reported throughout the week.

WEEKEND FORECAST
🌊 Saturday: 1–2½ ft swells"""
IG_0828_AT = datetime.fromtimestamp(1787948145, timezone.utc)

# Instagram, 2026-08-07 (Friday).
IG_0807 = """✅ Recent Reports
• Cabrillo Beach: 20 ft visibility, flat surf, no surge or current
• Honeymoon Cove (PV): 20–25 ft visibility
• White Point: ~10 ft visibility last weekend, likely improved with calmer conditions"""
IG_0807_AT = datetime.fromtimestamp(1786126626, timezone.utc)

# Facebook, Friday 2026-09-25, line breaks as the shop page renders them.
FB_0925 = """===================================
Conditions Reports
===================================
Diving conditions along the local coast have been GOOD this week!
Our Advanced OW class had good diving conditions at Veterans Park in Redondo Beach last night, with 1–2 ft surf/swells and 25 ft VIZ. Water temperatures ranged from 72°F at the surface to 67°F at 100 ft.
Also yesterday, we took the shop boat “Sea Hunt” out to the “Edith” Oil Rig off Long Beach and had great visibility of 70 ft! We could easily see the first platform from the surface. 🙂 Water temperatures ranged from 74°F at the surface to 68°F at 100 ft.
On the way back, we also stopped at Point Fermin and the Cabrillo breakwall, where we found 30–40 ft VIZ at Point Fermin and 15–25 ft VIZ at the breakwall. Definitely a great day of diving!
Looking ahead, the forecast calls for sunny skies with air temperatures in the upper 70s to low 80s throughout the week.
===================================
Conditions Forecast
===================================
CABRILLO – A South Facing protected cove in Palos Verdes / San Pedro shows 0-1 foot surf with 12 second intervals & ½ foot swells on Saturday (EXCELLENT). We expect VIZ to be usually “just “OK” at Best for Cabrillo."""
FB_0925_AT = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)

# Facebook, Friday 2026-10-02: a customer report and a week's average.
FB_1002 = """We’ve had favorable dive conditions all along the northern coast over the past month! Veteran’s Park at Redondo Beach had an average 20 ft + VIZ over the last week. Divers also reported 66F at depth and 69F at the surface with 50+ Feet of VIZ at the Olympic this morning."""
FB_1002_AT = datetime(2026, 10, 2, 18, 0, tzinfo=timezone.utc)


def _by_site(readings):
    return {r["site"]: r for r in readings}


def test_bullets_one_reading_per_site_and_second_hand_skipped():
    got = _by_site(pw.extract(IG_0904, IG_0904_AT))
    assert set(got) == {"Oil Rigs", "Point Fermin"}  # "Catalina ... reported" is second-hand
    assert (got["Oil Rigs"]["lo_ft"], got["Oil Rigs"]["hi_ft"]) == (60.0, 60.0)  # not the 60 ft depth
    assert got["Point Fermin"]["lo_ft"] == 50.0
    # No day in the bullet: a recent report, dated to the post and flagged.
    assert got["Oil Rigs"]["day"] == date(2026, 9, 4) and not got["Oil Rigs"]["dated"]
    assert got["Oil Rigs"]["sentence"].startswith("Oil Rigs: 60+ ft visibility")


def test_named_days_count_back_from_the_post():
    got = _by_site(pw.extract(IG_0821, IG_0821_AT))
    assert set(got) == {"Malaga Cove", "Point Fermin"}  # "throughout the week" has no single day
    assert got["Malaga Cove"]["day"] == date(2026, 8, 21) and got["Malaga Cove"]["dated"]
    assert (got["Malaga Cove"]["lo_ft"], got["Malaga Cove"]["hi_ft"]) == (20.0, 30.0)
    assert got["Point Fermin"]["day"] == date(2026, 8, 17)  # last Monday before Friday Aug 21
    rigs = pw.extract(IG_0828, IG_0828_AT)
    assert [(r["site"], r["day"], r["lo_ft"]) for r in rigs] == [("Oil Rigs", date(2026, 8, 23), 50.0)]


def test_weekend_recaps_skipped_and_cabrillo_is_san_pedro():
    got = _by_site(pw.extract(IG_0807, IG_0807_AT))
    assert set(got) == {"Cabrillo Beach", "Honeymoon Cove"}  # White Point was "last weekend"
    assert got["Cabrillo Beach"]["lat"] > 33.7  # San Pedro's, not San Diego's Cabrillo


def test_prose_reads_viz_not_surf_and_keeps_the_day_across_sentences():
    got = pw.extract(FB_0925, FB_0925_AT)
    by = _by_site(got)
    assert set(by) == {"Veterans Park", "Edith oil rig", "Point Fermin", "Cabrillo breakwall"}
    assert by["Veterans Park"]["lo_ft"] == 25.0  # not the 1-2 ft surf
    assert by["Edith oil rig"]["lo_ft"] == 70.0 and by["Edith oil rig"]["lat"] == 33.596
    assert (by["Point Fermin"]["lo_ft"], by["Point Fermin"]["hi_ft"]) == (30.0, 40.0)
    assert (by["Cabrillo breakwall"]["lo_ft"], by["Cabrillo breakwall"]["hi_ft"]) == (15.0, 25.0)
    # "last night", "Also yesterday", and "On the way back" all fall on Thursday.
    assert {r["day"] for r in got} == {date(2026, 9, 24)} and all(r["dated"] for r in got)


def test_customer_reports_and_averages_are_skipped():
    assert pw.extract(FB_1002, FB_1002_AT) == []
    assert pw.extract("• Point Fermin: up to 20 ft visibility today", FB_1002_AT) == []
    assert pw.extract("• Lunada Bay: 15 ft visibility today", FB_1002_AT)[0]["site"] == "Lunada Bay"
    assert pw.extract("• Hermosa Pier: 15 ft visibility today", FB_1002_AT) == []  # no coordinates
    caps = pw.extract("RECENT REPORTS\nOIL RIGS: 60+ FT VIZ TODAY", FB_1002_AT)  # a title line is not a reading
    assert [(r["site"], r["lo_ft"]) for r in caps] == [("Oil Rigs", 60.0)]


def test_reading_time_is_the_post_or_noon_pacific():
    r = pw.extract(FB_0925, FB_0925_AT)[0]
    assert pw.when_utc(r, FB_0925_AT).isoformat() == "2026-09-24T19:00:00+00:00"
    same_day = _by_site(pw.extract(IG_0821, IG_0821_AT))["Malaga Cove"]
    assert pw.when_utc(same_day, IG_0821_AT) == IG_0821_AT


IG_HTML = """<div class="sbi_item sbi_type_image sbi_new sbi_transition" role="listitem"
	id="sbi_18125028040825705" data-date="1788544830">
	<div class="sbi_photo_wrap">
		<a class="sbi_photo" href="https://www.instagram.com/p/Dc3-UYXtkWM/" target="_blank" rel="noopener nofollow">
			<span class="sbi-screenreader">LOCAL DIVING CONDITIONS UPDATE</span>
			<img decoding="async" src="placeholder.png" alt="LOCAL DIVING CONDITIONS UPDATE

🤿 Recent Reports
• Oil Rigs: 60+ ft visibility | 63°F at 60 ft" aria-hidden="true">
		</a>
	</div>
</div><div class="sbi_item sbi_type_image sbi_new sbi_transition" role="listitem"
	id="sbi_1" data-date="1786126626">
	<div class="sbi_photo_wrap"><img src="x.png" alt="Short"></div>
</div>"""


def test_instagram_feed_items_carry_exact_times_and_links():
    posts = pw.instagram_posts(IG_HTML)
    assert len(posts) == 1  # the second caption is too short to be a report
    assert posts[0].url == "https://www.instagram.com/p/Dc3-UYXtkWM/"
    assert posts[0].posted_at.isoformat() == "2026-09-04T18:00:30+00:00"
    assert "Oil Rigs: 60+ ft visibility" in posts[0].text


def _fb_item(age: str, pid: str) -> str:
    return (f'<div role="listitem" id="cff_65235417494_{pid}" class="cff-item cff-status-post">'
            f'<p class="cff-date" > {age} </p></div>\n<div class="cff-post-text" >\n\t<span class="cff-text" data-color="">\n\t\t'
            'Our Advanced OW class had good diving conditions at Veterans Park in Redondo Beach last night, with 1–2 ft '
            'surf/swells and 25 ft VIZ.<img class="cff-linebreak" alt="" aria-hidden="true" />Also yesterday, we went to the '
            '“Edith” Oil Rig and had great visibility of 70 ft!</span>\n\t<span class="cff-expand">... <button type="button" '
            'class="cff-readmore"><span class="cff-more">See More</span></button></span>\n</div>'
            f'<a class="cff-viewpost-facebook" href="https://www.facebook.com/1537180991547431/posts/{pid}" title="View on Facebook">')


def test_facebook_posts_read_only_while_the_age_is_in_hours():
    now = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
    page = _fb_item("2 hours ago", "111") + _fb_item("6 days ago", "222")
    posts = pw.facebook_posts(page, now)
    assert [p.url for p in posts] == ["https://www.facebook.com/1537180991547431/posts/111"]
    assert posts[0].posted_at.isoformat() == "2026-09-25T18:00:00+00:00"
    assert "See More" not in posts[0].text and "\nAlso yesterday" in posts[0].text
    assert {r["site"] for r in pw.extract(posts[0].text, posts[0].posted_at)} == {"Veterans Park", "Edith oil rig"}


def test_rows_are_one_per_site_and_day_with_their_own_link():
    s = pw.PacificWildernessScraper()
    post = pw.Post(url="https://www.facebook.com/1537180991547431/posts/1557465569518973", posted_at=FB_0925_AT,
                   text=FB_0925, channel="facebook")
    rows = [s.row(post, r, pw.when_utc(r, post.posted_at)) for r in pw.extract(post.text, post.posted_at)]
    assert len({r["obs_id"] for r in rows}) == 4 and len({r["source_url"] for r in rows}) == 4
    fermin = next(r for r in rows if r["spot_name"] == "Point Fermin")
    assert fermin["obs_id"] == "dive-shop-pacificwilderness-20260924-point-fermin-0"
    assert fermin["source_url"].endswith("/posts/1557465569518973#point-fermin")
    assert (fermin["observed_lo_ft"], fermin["observed_hi_ft"], fermin["observed_secchi_ft"]) == (30.0, 40.0, 35.0)
    assert fermin["day_stated"] is True and fermin["extraction_method"] == "regex"
