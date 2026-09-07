"""
Pure HTML -> product parsing for eBay storefront pages.

Kept dependency-free of any network code so it can be unit-tested against
saved real eBay HTML (see tests/).
"""

from __future__ import annotations

import re
from bs4 import BeautifulSoup

# Full-size image. eBay serves s-l64 .. s-l1600 for every listing image.
IMAGE_SIZE = "s-l1600"

BLOCK_MARKERS = (
    "pardon our interruption",
    "unusual traffic",
    "verify you are a human",
    "are you a robot",
    "checking your browser",
    "access to this page has been denied",
    "/splashui/challenge",
)
SOLD_OUT_MARKERS = ("we are sold out",)

_SIZE_RE = re.compile(r"/s-l\d+\.(?:webp|jpg|jpeg|png)(?:\?.*)?$", re.I)
_ID_RE = re.compile(r"^\d{9,13}$")
_ITM_RE = re.compile(r"/itm/(\d{9,13})(?!\d)")


def has_cards(html: str) -> bool:
    """Cheap substring test - no parsing - for 'this looks like a storefront'."""
    return "str-item-card" in html


def detect_state(html: str) -> str:
    """
    Classify a fetched page: 'blocked', 'sold_out' or 'ok'.

    A page carrying product cards is never a bot-check, whatever words appear
    on it: real listings are titled things like "Anti-Captcha Solver CD", and a
    single one of those must not be able to shut the watcher down.
    """
    if has_cards(html):
        return "ok"
    low = html.lower()
    if any(m in low for m in BLOCK_MARKERS):
        return "blocked"
    if any(m in low for m in SOLD_OUT_MARKERS):
        return "sold_out"
    return "ok"


def _image_url(card) -> str:
    """
    Build a full-size image URL.

    Preferred source is the `imageid` attribute, which is the stable eBay image
    GUID. The `src`/`srcset` thumbnails are only used as a fallback, and note
    that raw HTML and the rendered DOM disagree there: <source> carries the
    .webp and <img src> the .jpg.
    """
    img = card.find("img")
    if img is None:
        return ""

    image_id = img.get("imageid")
    if image_id:
        return f"https://i.ebayimg.com/images/g/{image_id}/{IMAGE_SIZE}.jpg"

    candidate = img.get("src") or img.get("data-src") or ""
    if not candidate:
        source = card.find("source")
        if source and source.get("srcset"):
            candidate = source["srcset"].split()[0]
    if not candidate:
        return ""
    return _SIZE_RE.sub(f"/{IMAGE_SIZE}.jpg", candidate)


def _title(card, link) -> str:
    # The link's aria-label is the cleanest full title.
    if link is not None and link.get("aria-label"):
        return link["aria-label"].strip()
    node = card.select_one(".str-item-card__property-title .str-text-span") \
        or card.select_one("h3.str-card-title")
    return node.get_text(strip=True) if node else ""


def parse_products(html: str) -> list[dict]:
    """Extract every product card from a storefront page."""
    soup = BeautifulSoup(html, "lxml")
    products: list[dict] = []

    for card in soup.select("article.str-item-card"):
        link = card.select_one("a.str-item-card__link") or card.select_one('a[href*="/itm/"]')

        item_id = (card.get("data-testid") or "").removeprefix("ig-").strip()
        # eBay item numbers are 9-13 digits. Anything else is a sponsored tile
        # or a markup change, and must never become a product URL.
        if not _ID_RE.match(item_id):
            item_id = ""
        if not item_id and link is not None:
            m = _ITM_RE.search(link.get("href", ""))
            item_id = m.group(1) if m else ""

        title = _title(card, link)
        if not item_id or not title:
            continue

        price_node = card.select_one(".str-item-card__property-displayPrice")

        products.append({
            "id": item_id,
            "title": title,
            "image": _image_url(card),
            "url": f"https://www.ebay.com/itm/{item_id}",
            "price": price_node.get_text(strip=True) if price_node else "",
        })

    return products
