"""Requirement 5: the Telegram layer (announce_new + Telegram._call)."""
from __future__ import annotations

import json
import re

import pytest
import requests

import scrape
from scrape import (ALBUM_SIZE, CAPTION_LIMIT, TEXT_LIMIT, Telegram,
                    clip_escaped,
                    announce_new, clip, esc, send_lines, _caption, _link_line)
from conftest import STORE_A, RecordingTelegram, prod


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda *_a, **_k: None)


def tg():
    return RecordingTelegram("t", "c")


def items(n, start=0, **kw):
    return [prod(f"{1550000000 + i}", f"Product {i} Omega-3", **kw)
            for i in range(start, start + n)]


# ------------------------------------------------------------ album batching #

def test_album_batches_of_ten():
    t = tg()
    announce_new(t, STORE_A, items(25), max_photos=100)
    albums = t.albums()
    assert [len(a) for a in albums] == [10, 10, 5]
    assert all(m["type"] == "photo" for a in albums for m in a)
    assert ALBUM_SIZE == 10



def test_single_new_item_uses_sendphoto_not_album():
    t = tg()
    announce_new(t, STORE_A, items(1), max_photos=100)
    assert t.methods() == ["sendMessage", "sendPhoto", "sendMessage"]
    assert t.photos()[0]["photo"] == items(1)[0]["image"]
    assert t.texts()[1].startswith("1. <a href=")


def test_numbered_link_list_follows_each_album_and_numbers_continue():
    t = tg()
    announce_new(t, STORE_A, items(25), max_photos=100)
    lists = [x for x in t.texts() if re.match(r"^\d+\. <a href=", x)]
    assert len(lists) == 3
    nums = [int(n) for x in lists for n in re.findall(r"^(\d+)\. ", x, re.M)]
    assert nums == list(range(1, 26))


def test_every_product_url_reaches_the_user_at_normal_volume():
    new = items(25)
    t = tg()
    announce_new(t, STORE_A, new, max_photos=100)
    blob = t.blob()
    missing = [p["url"] for p in new if p["url"] not in blob]
    assert missing == []


def test_header_counts_and_pluralisation():
    t = tg()
    announce_new(t, STORE_A, items(1), max_photos=5)
    assert "1 new product<" in t.texts()[0]
    t = tg()
    announce_new(t, STORE_A, items(3), max_photos=5)
    assert "3 new products<" in t.texts()[0]


# ------------------------------------------------------------ MAX_PHOTOS ----

def test_max_photos_overflow_goes_to_a_text_list():
    t = tg()
    announce_new(t, STORE_A, items(25), max_photos=12)
    assert sum(len(a) for a in t.albums()) + len(t.photos()) == 12
    tail = [x for x in t.texts() if x.startswith("...and ")]
    assert tail and tail[0].startswith("...and 13 more:")


def test_items_without_an_image_are_pushed_into_the_text_list():
    new = items(3) + items(2, start=90, image="")
    t = tg()
    announce_new(t, STORE_A, new, max_photos=100)
    tail = [x for x in t.texts() if x.startswith("...and ")]
    assert tail and "2 more" in tail[0]




def test_bulk_overflow_delivers_every_product_url():
    """A bulk restock must not silently drop links (was DEFECT 2)."""
    new = items(500)
    t = tg()
    announce_new(t, STORE_A, new, max_photos=100)   # MAX_PHOTOS=100 in watch.yml
    blob = t.blob()
    missing = [p["url"] for p in new if p["url"] not in blob]
    assert missing == [], f"{len(missing)} of 500 links never delivered"
    assert all(len(m) <= TEXT_LIMIT for m in t.texts())


def test_overflow_list_is_split_across_messages_not_truncated():
    new = items(300)
    t = tg()
    announce_new(t, STORE_A, new, max_photos=10)
    tail = [x for x in t.texts() if x.startswith("...and ") or x.startswith("11. ")]
    assert len(t.texts()) > 3, "the tail was split over several messages"
    assert "\n...\n" not in t.blob() and not t.blob().endswith("\n...")
    for p in new:
        assert p["url"] in t.blob()


# -------------------------------------------------------------- escaping ----

def test_html_escaping_in_captions_and_lists():
    nasty = prod("155000000001", 'Vitamin <b>D3</b> & K2 > 5000IU <script>x</script>')
    t = tg()
    announce_new(t, STORE_A, [nasty] * 2, max_photos=10)
    blob = t.blob()
    assert "<script>" not in blob
    assert "&lt;script&gt;" in blob
    assert "&amp; K2 &gt; 5000IU" in blob


def test_esc_orders_ampersand_first():
    assert esc("<&>") == "&lt;&amp;&gt;"
    assert esc("a & b") == "a &amp; b"
    assert "&amp;lt;" not in esc("<")


def test_store_name_is_escaped_in_the_header():
    store = dict(STORE_A, name="Herbs & <Direct>")
    t = tg()
    announce_new(t, store, items(2), max_photos=10)
    assert "Herbs &amp; &lt;Direct&gt;" in t.texts()[0]


def test_price_is_escaped():
    p = prod("155000000002", "T", price="$5 & up")
    t = tg()
    announce_new(t, STORE_A, [p, p], max_photos=10)
    assert "$5 &amp; up" in t.blob()


# ------------------------------------------------------- length limits ------



def test_caption_is_within_the_limit_and_well_formed_for_a_huge_title():
    """The title is clipped before escaping, so markup is never cut (DEFECT 4)."""
    p = prod("155000000003", "Herbal Supplement " * 80)          # 1440 chars
    cap = _caption(p, STORE_A)
    assert len(cap) <= CAPTION_LIMIT
    assert cap.count("<b>") == cap.count("</b>") == 1
    assert cap.endswith(p["url"])




def test_caption_is_within_the_limit_for_an_entity_heavy_title():
    """Budgeting happens in escaped space now (was DEFECT A)."""
    for n in (0, 1, 40, 80, 200, 400, 2000, 20000):
        p = prod("155000000004", "&" * n, price="&" * n)
        cap = _caption(p, dict(STORE_A, name="&" * 200))
        assert len(cap) <= CAPTION_LIMIT, f"n={n}: {len(cap)}"



def test_entity_heavy_caption_is_not_cut_mid_entity_by_the_send_layer():
    p = prod("155000000004", "&" * 200, price="&" * 40)
    cap = _caption(p, dict(STORE_A, name="&" * 200))
    assert cap == cap[:CAPTION_LIMIT], "the send layer has nothing left to slice"
    assert not re.search(r"&[a-z]{0,3}$", cap)



def test_entity_heavy_caption_still_carries_the_product_url():
    for n in (0, 80, 200, 5000):
        p = prod("155000000004", "&" * n, price="&" * n)
        cap = _caption(p, dict(STORE_A, name="&" * 200))
        assert cap.endswith(p["url"]), f"n={n}: the product link was cut off"


def test_caption_bold_tag_always_survives_the_slice():
    """The <b> wrapper itself is safe: a 200-char clip escapes to at most 1000."""
    for title in ("Z" * 5000, "&" * 5000, "<" * 5000):
        cap = _caption(prod("155000000007", title), STORE_A)[:CAPTION_LIMIT]
        assert cap.count("<b>") == cap.count("</b>") == 1


def test_caption_never_splits_an_html_entity():
    p = prod("155000000004", "A" * 1015 + "&&&&&")
    cap = _caption(p, STORE_A)
    assert not re.search(r"&[a-z]*$", cap)
    assert len(cap) <= CAPTION_LIMIT


def test_clip_helper():
    assert clip("abc", 10) == "abc"
    assert clip("a" * 20, 10) == "a" * 9 + "\u2026"
    assert len(clip("a" * 500, 200)) == 200
    assert clip("", 5) == ""








def test_numbered_link_list_respects_the_4096_char_limit():
    """10 entity-heavy 80-char titles used to make one 4640-char message."""
    title = "&" * 80
    batch = [prod(f"15500000{i:04d}", title) for i in range(10)]
    t = tg()
    announce_new(t, STORE_A, batch, max_photos=10)
    lists = [x for x in t.texts() if re.match(r"^\d+\. <a href=", x)]
    assert lists
    assert all(len(x) <= TEXT_LIMIT for x in t.texts())
    for p in batch:
        assert p["url"] in t.blob()


def test_tail_list_stays_under_4096_for_normal_titles():
    t = tg()
    announce_new(t, STORE_A, items(400), max_photos=10)
    for msg in t.texts():
        assert len(msg) <= 4096, f"message of {len(msg)} chars would be rejected"




def test_tail_list_stays_under_4096_with_one_huge_title():
    huge = prod("155000000005", "X" * 6000, image="")
    t = tg()
    announce_new(t, STORE_A, items(2) + [huge], max_photos=10)
    assert all(len(m) <= TEXT_LIMIT for m in t.texts())
    assert huge["url"] in t.blob()


# --------------------------------------------------------------- send_lines #

def test_send_lines_splits_and_never_drops_a_line():
    t = tg()
    lines = [f"{i}. " + "y" * 300 for i in range(200)]
    assert send_lines(t, "header:", lines) is True
    msgs = t.texts()
    assert len(msgs) > 1
    assert all(len(m) <= TEXT_LIMIT for m in msgs)
    blob = "\n".join(msgs)
    for line in lines:
        assert line in blob
    assert msgs[0].startswith("header:")


def test_send_lines_with_no_header():
    t = tg()
    send_lines(t, "", ["a", "b", "c"])
    assert t.texts() == ["a\nb\nc"]


def test_send_lines_with_no_lines_sends_only_the_header():
    t = tg()
    send_lines(t, "header only", [])
    assert t.texts() == ["header only"]


def test_send_lines_sends_nothing_for_an_empty_request():
    t = tg()
    assert send_lines(t, "", []) is True
    assert t.calls == []


def test_send_lines_reports_failure_but_still_sends_the_rest():
    t = tg()
    t.fail_once["sendMessage"] = 1
    lines = [f"{i}. " + "y" * 300 for i in range(100)]
    assert send_lines(t, "h", lines) is False
    assert len(t.texts()) > 1, "a failed chunk does not abort the remaining ones"


def test_link_line_is_bounded():
    p = prod("155000000006", "Z" * 5000, price="$" * 500)
    line = _link_line(1, p)
    assert len(line) < 1000
    assert line.count("<a ") == line.count("</a>") == 1


# ------------------------------------------------ failure of sendMediaGroup --

def test_album_failure_falls_back_to_individual_photos():
    t = tg()
    t.fail_methods.add("sendMediaGroup")
    announce_new(t, STORE_A, items(10), max_photos=100)
    assert len(t.photos()) == 10
    assert len(t.albums()) == 1          # attempted once, failed



def test_numbered_link_list_is_sent_even_when_the_album_fails():
    """The links must not depend on sendMediaGroup succeeding (was DEFECT 5)."""
    new = items(10)
    t = tg()
    t.fail_methods.add("sendMediaGroup")
    announce_new(t, STORE_A, new, max_photos=100)
    lists = [x for x in t.texts() if x.startswith("1. <a href=")]
    assert lists, "the numbered link list went out anyway"
    for p in new:
        assert p["url"] in lists[0]


def test_numbered_link_list_is_sent_when_the_album_succeeds():
    new = items(10)
    t = tg()
    announce_new(t, STORE_A, new, max_photos=100)
    assert len(t.albums()) == 1
    lists = [x for x in t.texts() if x.startswith("1. <a href=")]
    assert lists and all(p["url"] in lists[0] for p in new)



def test_rejected_photo_url_falls_back_to_text():
    t = tg()
    t.fail_methods.update({"sendMediaGroup", "sendPhoto"})
    failed = announce_new(t, STORE_A, items(3), max_photos=100)
    assert len(t.photos()) == 3
    # 3 caption fallbacks + the numbered link list
    assert sum(1 for x in t.texts() if "www.ebay.com/itm/" in x) == 4
    # the text fallback succeeded, so nothing is reported as undelivered
    assert failed == []


def test_ids_are_reported_as_failed_when_every_channel_is_refused():
    t = tg()
    t.fail_methods.update({"sendMediaGroup", "sendPhoto", "sendMessage"})
    new = items(3)
    failed = announce_new(t, STORE_A, new, max_photos=100)
    assert set(failed) == {p["id"] for p in new}


def test_nothing_is_reported_as_failed_on_a_healthy_send():
    t = tg()
    assert announce_new(t, STORE_A, items(25), max_photos=100) == []


def test_empty_image_url_short_circuits_to_text():
    t = Telegram("t", "c", enabled=False)
    calls = []
    t._call = lambda m, p: (calls.append((m, p)), True)[1]
    assert t.photo("", "caption text") is True
    assert [m for m, _ in calls] == ["sendMessage"]


# --------------------------------------------------- Telegram._call HTTP ----

class FakeResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body
        self.text = text or json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def real_response(status, body_bytes, content_type="application/json"):
    r = requests.Response()
    r.status_code = status
    r._content = body_bytes
    r.headers["Content-Type"] = content_type
    r.url = "https://api.telegram.org/botX/sendMessage"
    return r


def test_call_success(monkeypatch):
    posts = []
    monkeypatch.setattr(scrape.requests, "post",
                        lambda url, **kw: posts.append((url, kw)) or
                        FakeResp(200, {"ok": True}))
    assert Telegram("tok", "chat").text("hi") is True
    assert posts[0][0] == "https://api.telegram.org/bottok/sendMessage"
    assert posts[0][1]["data"]["parse_mode"] == "HTML"


def test_call_400_is_not_retried(monkeypatch):
    n = []
    def post(url, **kw):
        n.append(1)
        return FakeResp(400, {"ok": False, "description": "can't parse entities"},
                        text='{"ok":false}')
    monkeypatch.setattr(scrape.requests, "post", post)
    assert Telegram("tok", "chat").text("hi") is False
    assert len(n) == 1, "a 400 is fatal for that message, no retry"


def test_429_is_retried_after_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda s: slept.append(s))
    seq = [FakeResp(429, {"parameters": {"retry_after": 7}}),
           FakeResp(200, {"ok": True})]
    monkeypatch.setattr(scrape.requests, "post", lambda url, **kw: seq.pop(0))
    assert Telegram("tok", "chat").text("hi") is True
    assert slept == [8]


def test_429_three_times_gives_up(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    monkeypatch.setattr(scrape.requests, "post",
                        lambda url, **kw: FakeResp(429, {"parameters": {"retry_after": 1}}))
    assert Telegram("tok", "chat").text("hi") is False


def test_429_with_non_json_body_does_not_crash(monkeypatch):
    """A CDN 429 page is HTML, not JSON."""
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        scrape.requests, "post",
        lambda url, **kw: real_response(429, b"<html>too many requests</html>", "text/html"))
    assert Telegram("tok", "chat").text("hi") is False


def test_200_with_non_json_body_does_not_crash(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        scrape.requests, "post",
        lambda url, **kw: real_response(200, b"<html>hi</html>", "text/html"))
    assert Telegram("tok", "chat").text("hi") is False


def test_network_error_is_retried_three_times(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda s: None)
    n = []
    def post(url, **kw):
        n.append(1)
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(scrape.requests, "post", post)
    assert Telegram("tok", "chat").text("hi") is False
    assert len(n) == 3


def test_disabled_telegram_never_posts(monkeypatch):
    monkeypatch.setattr(scrape.requests, "post",
                        lambda *a, **k: pytest.fail("network touched in dry-run"))
    t = Telegram(None, None, enabled=False)
    assert t.text("x") is True
    assert t.album([prod("1")], ["c"]) is True


def test_album_payload_shape():
    t = tg()
    new = items(3)
    t.album(new, [_caption(p, STORE_A) for p in new])
    media = json.loads(t.calls[0][1]["media"])
    assert [m["media"] for m in media] == [p["image"] for p in new]
    assert all(m["parse_mode"] == "HTML" for m in media)
    assert all(len(m["caption"]) <= 1024 for m in media)


# ------------------------------------------------- API-call volume of a surge

def test_api_call_volume_for_a_large_restock():
    """send_lines never drops a line -- but it can emit a lot of messages."""
    new = items(1000)
    t = tg()
    announce_new(t, STORE_A, new, max_photos=100)
    msgs = sum(1 for m in t.methods() if m == "sendMessage")
    albums = len(t.albums())
    print(f"\n  1000 new products -> {albums} sendMediaGroup + {msgs} sendMessage "
          f"= {len(t.calls)} API calls")
    assert all(p["url"] in t.blob() for p in new)
    # paced only by the 2s sleep after each album; the tail list is unpaced
    assert msgs >= 30
    # 1 header + 10 link lists (paced 2s apart) + ~19 tail messages sent
    # back-to-back with no pacing at all
    assert albums == 10



def test_failed_ids_are_not_double_counted():
    """An id refused by both the photo and the link list counts once."""
    t = tg()
    t.fail_methods.update({"sendMediaGroup", "sendPhoto", "sendMessage"})
    new = items(3)
    failed = announce_new(t, STORE_A, new, max_photos=100)
    assert failed == list(dict.fromkeys(failed))
    assert len(failed) == 3
    assert set(failed) == {p["id"] for p in new}



def test_a_total_telegram_outage_leaves_every_id_unremembered(hz):
    """len(new_items) - len(failed) must never go negative, and nothing that
    was not delivered may be marked as seen."""
    from conftest import STORE_A as SA
    hz.set_stores([SA])
    hz.use_products({"storea": [prod("a")]})
    hz.run()

    hz.tg_fail_methods = {"sendMessage", "sendPhoto", "sendMediaGroup"}
    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    assert hz.run() == 1
    seen = set(hz.snapshot("storea")["seen"])
    assert "b" not in seen and "c" not in seen
    assert "a" in seen, "already-known ids stay remembered"

    # Telegram recovers: both are announced on the next run, exactly once
    hz.tg_fail_methods = set()
    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    assert hz.run() == 0
    assert sorted(hz.announced_ids()) == ["b", "c"]
    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    assert hz.run() == 0
    assert hz.announced_ids() == []

# ------------------------------------------------- clip_escaped boundaries --

def test_clip_escaped_passes_short_text_through():
    assert clip_escaped("abc", 10) == "abc"
    assert clip_escaped("abc", 3) == "abc"
    assert clip_escaped("", 0) == ""


def test_clip_escaped_result_never_exceeds_the_limit():
    for text in ("a" * 100, "&amp;" * 100, "&lt;x&gt;" * 50, "a&amp;b" * 40):
        for limit in range(1, 60):
            out = clip_escaped(text, limit)
            assert len(out) <= limit, f"{text[:12]!r} limit={limit} -> {len(out)}"


def test_clip_escaped_never_leaves_a_partial_entity():
    text = "&amp;" * 100
    for limit in range(1, 60):
        out = clip_escaped(text, limit)
        assert not re.search(r"&[a-z]{0,3}$", out), f"limit={limit}: {out[-6:]!r}"


def test_clip_escaped_keeps_complete_entities():
    assert clip_escaped("&amp;&amp;&amp;", 7) == "&amp;…"


def test_clip_escaped_does_not_trim_a_finished_entity_before_plain_text():
    assert clip_escaped("&amp;hello world", 12) == "&amp;hello…"


def test_clip_escaped_with_a_non_positive_limit():
    """Was LATENT L2: a zero limit used to return the bare ellipsis."""
    for limit in (0, -1, -100):
        assert clip_escaped("abcdef", limit) == ""
        assert clip_escaped("", limit) == ""


# ---------------------------------------------------- _caption boundaries ---

def test_caption_fills_the_budget_exactly_for_a_long_title():
    p = prod("155000000009", "Z" * 5000, price="$19.99")
    cap = _caption(p, STORE_A)
    assert len(cap) == CAPTION_LIMIT
    assert cap.count("<b>") == cap.count("</b>") == 1
    assert cap.endswith(p["url"])


def test_caption_never_needs_slicing_for_any_shape_of_input():
    stores = [STORE_A, dict(STORE_A, name="&" * 300), dict(STORE_A, name="<>" * 200)]
    titles = ["", "x", "&", "&" * 5000, "<" * 5000, "Z" * 5000, "ü" * 5000]
    prices = ["", "$1", "&" * 500, "<" * 500]
    for st in stores:
        for t in titles:
            for pr in prices:
                cap = _caption(prod("155000000010", t, price=pr), st)
                assert len(cap) <= CAPTION_LIMIT
                assert cap == cap[:CAPTION_LIMIT]
                assert cap.count("<b>") == cap.count("</b>") == 1
                assert cap.endswith("https://www.ebay.com/itm/155000000010")


def test_caption_with_an_empty_title_is_still_valid():
    item = dict(prod("155000000011", price=""), title="")
    cap = _caption(item, STORE_A)
    assert cap.startswith("<b></b>") and cap.endswith("/itm/155000000011")
