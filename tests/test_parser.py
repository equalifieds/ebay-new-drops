"""
Requirement 3: parser.py.

Runnable two ways:
    python -m pytest tests/test_parser.py
    python tests/test_parser.py          <- what watch.yml invokes

eBay item numbers are 9-13 digits and parser.py now enforces that, so every
fixture id here is a realistic 12-digit number (see ebayfakes.iid).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from parser import IMAGE_SIZE, detect_state, has_cards, parse_products
from ebayfakes import BLOCKED_HTML, SOLD_OUT_HTML, card, iid, page


def one(html):
    items = parse_products(html)
    assert len(items) == 1, f"expected 1 product, got {len(items)}"
    return items[0]


# ------------------------------------------------------------------- ids ----

def test_id_from_data_testid():
    p = one(page([card("155123456789", "Vitamin C 1000mg")]))
    assert p["id"] == "155123456789"
    assert p["url"] == "https://www.ebay.com/itm/155123456789"


def test_id_falls_back_to_itm_href():
    p = one(page([card("155123456789", "Vitamin C", with_testid=False)]))
    assert p["id"] == "155123456789"


def test_id_from_href_with_query_and_tracking_suffix():
    html = page(['<article class="str-item-card">'
                 '<a class="str-item-card__link" aria-label="Zinc 50mg"'
                 ' href="https://www.ebay.com/itm/226601234567?hash=item34ab&var=0"></a>'
                 '<img imageid="gZ" src="x">'
                 '</article>'])
    assert one(html)["id"] == "226601234567"


@pytest.mark.parametrize("n", [9, 10, 11, 12, 13])
def test_id_lengths_9_to_13_are_accepted(n):
    num = "1" * n
    html = page([card(num, "Accepted")])
    assert one(html)["id"] == num


@pytest.mark.parametrize("num", ["1", "1234", "12345678"])
def test_ids_shorter_than_9_digits_are_rejected(num):
    html = page([card(num, "Rejected")])
    assert parse_products(html) == []



def test_over_long_number_in_href_is_not_truncated_into_an_id():
    """_ITM_RE is right-anchored, so a 14-digit run yields no id, not a prefix."""
    html = page([card("12345678901234", "Rejected")])
    assert parse_products(html) == []


def test_trailing_path_after_the_item_number_still_matches():
    html = page(['<article class="str-item-card">'
                 '<a class="str-item-card__link" aria-label="Real"'
                 ' href="https://www.ebay.com/itm/155123456789/?var=1"></a></article>'])
    assert [p["id"] for p in parse_products(html)] == ["155123456789"]


def test_card_with_no_id_anywhere_is_dropped():
    html = page(['<article class="str-item-card">'
                 '<a class="str-item-card__link" href="/sch/i.html" aria-label="Ad"></a>'
                 '</article>'])
    assert parse_products(html) == []


def test_data_testid_without_ig_prefix_is_used_verbatim():
    html = page(['<article class="str-item-card" data-testid="155999888777">'
                 '<a class="str-item-card__link" aria-label="Fish Oil"'
                 ' href="https://www.ebay.com/itm/155999888777"></a></article>'])
    assert one(html)["id"] == "155999888777"


def test_non_numeric_data_testid_is_rejected():
    """A sponsored tile must never become a product URL (was DEFECT 12)."""
    html = page(['<article class="str-item-card" data-testid="ig-promo-banner">'
                 '<a class="str-item-card__link" aria-label="Sponsored"'
                 ' href="https://www.ebay.com/str/foo"></a></article>'])
    assert parse_products(html) == []


def test_bad_data_testid_falls_through_to_the_itm_href():
    """A markup change on data-testid must not lose a real listing."""
    html = page(['<article class="str-item-card" data-testid="ig-item-card-3">'
                 '<a class="str-item-card__link" aria-label="Real Listing"'
                 ' href="https://www.ebay.com/itm/155123456789"></a></article>'])
    p = one(html)
    assert p["id"] == "155123456789"


def test_short_number_in_href_is_not_mistaken_for_an_item_id():
    html = page(['<article class="str-item-card">'
                 '<a class="str-item-card__link" aria-label="Category"'
                 ' href="https://www.ebay.com/itm/1234"></a></article>'])
    assert parse_products(html) == []


def test_id_whitespace_is_stripped():
    html = page(['<article class="str-item-card" data-testid="ig- 155123456789 ">'
                 '<a class="str-item-card__link" aria-label="Padded"'
                 ' href="https://www.ebay.com/itm/155123456789"></a></article>'])
    assert one(html)["id"] == "155123456789"


# ----------------------------------------------------------------- titles ---

def test_title_from_aria_label_is_preferred():
    html = page([card(iid(1), "Full Aria Title 120 Capsules", heading="Truncated...")])
    assert one(html)["title"] == "Full Aria Title 120 Capsules"


def test_title_falls_back_to_property_title_span():
    html = page([card(iid(1), "unused").replace('aria-label="unused"', "")])
    assert one(html)["title"] == "unused"


def test_title_falls_back_to_h3_str_card_title():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(42)}">'
                 f'<a class="str-item-card__link" href="/itm/{iid(42)}"></a>'
                 '<h3 class="str-card-title">Turmeric Complex</h3></article>'])
    assert one(html)["title"] == "Turmeric Complex"


def test_card_with_no_title_is_dropped():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(42)}">'
                 f'<a class="str-item-card__link" href="/itm/{iid(42)}"></a>'
                 '</article>'])
    assert parse_products(html) == []


def test_aria_label_is_stripped():
    html = page([card(iid(7), "  Padded Title  ")])
    assert one(html)["title"] == "Padded Title"


def test_unicode_title_survives():
    html = page([card(iid(8), "Ashwagandha Ksm-66 – 600µg – อาหาร")])
    assert "µg" in one(html)["title"]


# ----------------------------------------------------------------- images ---

def test_image_prefers_imageid_guid():
    p = one(page([card(iid(9), "T", gid="ignored")]))
    assert p["image"] == f"https://i.ebayimg.com/images/g/g{iid(9)}/{IMAGE_SIZE}.jpg"


def test_image_src_fallback_upgrades_to_s_l1600():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(10)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(10)}"></a>'
                 '<img src="https://i.ebayimg.com/images/g/AbC/s-l140.jpg">'
                 '</article>'])
    assert one(html)["image"] == f"https://i.ebayimg.com/images/g/AbC/{IMAGE_SIZE}.jpg"


def test_image_data_src_fallback():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(11)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(11)}"></a>'
                 '<img data-src="https://i.ebayimg.com/images/g/Q/s-l500.webp">'
                 '</article>'])
    assert one(html)["image"] == f"https://i.ebayimg.com/images/g/Q/{IMAGE_SIZE}.jpg"


def test_image_source_srcset_fallback_when_img_has_no_src():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(12)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(12)}"></a>'
                 '<picture>'
                 '<source srcset="https://i.ebayimg.com/images/g/W/s-l500.webp 1x,'
                 ' https://i.ebayimg.com/images/g/W/s-l960.webp 2x">'
                 '<img alt="">'
                 '</picture></article>'])
    assert one(html)["image"] == f"https://i.ebayimg.com/images/g/W/{IMAGE_SIZE}.jpg"


def test_image_missing_entirely_gives_empty_string():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(13)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(13)}"></a>'
                 '</article>'])
    assert one(html)["image"] == ""


def test_unrecognised_image_url_is_passed_through_unchanged():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(14)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(14)}"></a>'
                 '<img src="https://cdn.example.com/photo.gif"></article>'])
    assert one(html)["image"] == "https://cdn.example.com/photo.gif"


def test_size_upgrade_keeps_query_string_off():
    html = page([f'<article class="str-item-card" data-testid="ig-{iid(15)}">'
                 f'<a class="str-item-card__link" aria-label="T" href="/itm/{iid(15)}"></a>'
                 '<img src="https://i.ebayimg.com/images/g/K/s-l64.jpg?cb=1"></article>'])
    assert one(html)["image"] == f"https://i.ebayimg.com/images/g/K/{IMAGE_SIZE}.jpg"


# ------------------------------------------------------------------ price ---

def test_price_extracted():
    assert one(page([card(iid(16), "T", price="$24.95")]))["price"] == "$24.95"


def test_missing_price_is_empty_string():
    assert one(page([card(iid(17), "T", price="")]))["price"] == ""


# ------------------------------------------------------------ detect_state --

def test_has_cards():
    assert has_cards(page([card(iid(1), "T")]))
    assert not has_cards(BLOCKED_HTML)
    assert not has_cards("<html><body>nothing</body></html>")


def test_detect_state_blocked():
    assert detect_state(BLOCKED_HTML) == "blocked"


@pytest.mark.parametrize("marker", [
    "Pardon Our Interruption", "unusual traffic", "verify you are a human",
    "Are you a robot", "Checking your browser",
    "Access to this page has been denied", "/splashui/challenge",
])
def test_detect_state_blocked_markers_case_insensitive(marker):
    assert detect_state(f"<html><body>{marker}</body></html>") == "blocked"


def test_detect_state_sold_out():
    assert detect_state(SOLD_OUT_HTML) == "sold_out"


def test_detect_state_ok():
    assert detect_state(page([card(iid(18), "Magnesium Glycinate")])) == "ok"


def test_detect_state_blocked_wins_over_sold_out_on_a_cardless_page():
    html = "<html>We are sold out. Pardon our interruption.</html>"
    assert detect_state(html) == "blocked"


def test_detect_state_not_fooled_by_a_listing_title():
    """A page carrying real cards is never a bot-check (was DEFECT 11)."""
    html = page([card(iid(19), "Are You A Robot Funny Gym T-Shirt Supplement Gift")])
    assert parse_products(html), "the page really does contain a valid product"
    assert detect_state(html) == "ok"


def test_detect_state_not_fooled_by_a_sold_out_phrase_in_a_listing():
    html = page([card(iid(20), "Sticker Pack: We Are Sold Out Shop Sign")])
    assert detect_state(html) == "ok"


def test_detect_state_scans_the_whole_page():
    """A challenge past 400 KB on a card-less page is still detected."""
    html = "<div>" + ("x" * 400_000) + "</div>Pardon Our Interruption"
    assert detect_state(html) == "blocked"


def test_detect_state_ok_for_an_unremarkable_cardless_page():
    assert detect_state("<html><body><h1>Store closed for maintenance</h1>"
                        "</body></html>") == "ok"


# -------------------------------------------------------------- malformed ---

def test_malformed_html_unclosed_tags_still_parses():
    html = (f'<html><body><section><article class="str-item-card" '
            f'data-testid="ig-{iid(21)}">'
            f'<a class="str-item-card__link" aria-label="Broken Card" '
            f'href="/itm/{iid(21)}">'
            '<div><img imageid="gX">')
    p = one(html)
    assert p["id"] == iid(21) and p["title"] == "Broken Card"


def test_empty_and_garbage_input():
    assert parse_products("") == []
    assert parse_products("not html at all") == []
    assert parse_products("<html><body><p>hi</p></body></html>") == []


def test_two_cards_are_both_returned():
    html = page([card(iid(22), "Outer"), card(iid(23), "Inner")])
    assert [p["id"] for p in parse_products(html)] == [iid(22), iid(23)]


def test_duplicate_cards_on_one_page_are_returned_twice():
    """parse_products does not de-duplicate; scrape_store is responsible."""
    html = page([card(iid(24), "Dup"), card(iid(24), "Dup")])
    assert len(parse_products(html)) == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
