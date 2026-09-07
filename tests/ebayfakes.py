"""
Synthetic eBay storefront markup + fake fetchers.

Nothing in here touches the network. The HTML shape mirrors the selectors
parser.py actually depends on:

    article.str-item-card[data-testid="ig-<id>"]
      a.str-item-card__link[href="/itm/<id>"][aria-label="<title>"]
      picture > source[srcset]  /  img[imageid][src]
      .str-item-card__property-displayPrice
      .str-item-card__property-title .str-text-span
"""
from __future__ import annotations

import random
from urllib.parse import parse_qs, urlparse

CARD = """\
<article class="str-item-card" {testid}>
  <a class="str-item-card__link" href="https://www.ebay.com/itm/{iid}" {aria}></a>
  <picture>
    <source srcset="https://i.ebayimg.com/images/g/{gid}/s-l500.webp 1x">
    <img {imageid} src="https://i.ebayimg.com/images/g/{gid}/s-l140.jpg" alt="">
  </picture>
  <div class="str-item-card__property-title"><span class="str-text-span">{heading}</span></div>
  {price}
</article>"""

PAGE = """<!doctype html><html><head><title>{store}</title></head><body>
<div class="str-marginals">{banner}</div>
<section class="str-item-grid">
{cards}
</section>
</body></html>"""


def card(iid, title, *, with_testid=True, with_aria=True, with_imageid=True,
         price="$19.99", heading=None, gid=None):
    return CARD.format(
        testid=f'data-testid="ig-{iid}"' if with_testid else "",
        iid=iid,
        aria=f'aria-label="{title}"' if with_aria else "",
        gid=gid or f"g{iid}",
        imageid=f'imageid="g{iid}"' if with_imageid else "",
        heading=heading if heading is not None else title,
        price=(f'<div class="str-item-card__property-displayPrice">{price}</div>'
               if price else ""),
    )


def page(items, store="TestStore", banner=""):
    """items: iterable of (id, title) or raw card strings."""
    cards = []
    for it in items:
        cards.append(it if isinstance(it, str) else card(it[0], it[1]))
    return PAGE.format(store=store, banner=banner, cards="\n".join(cards))


BLOCKED_HTML = ("<html><body><h1>Pardon Our Interruption</h1>"
                "<p>As you were browsing something about your browser made us "
                "think you were a bot.</p></body></html>")

SOLD_OUT_HTML = ("<html><body><section class='str-item-grid'></section>"
                 "<h2>We are sold out</h2></body></html>")


#: eBay item numbers are 9-13 digits; parser.py enforces that, so fixtures must
#: use realistic ones.
ID_BASE = 155000000000


def iid(n) -> str:
    """A valid 12-digit eBay item number for fixture index `n`."""
    return str(ID_BASE + int(n))


def make_catalogue(n, base=ID_BASE, start=0):
    """n synthetic products with valid 12-digit ids."""
    return [(str(base + i), f"Product {i} Multivitamin 120ct")
            for i in range(start, start + n)]


# --------------------------------------------------------------------------- #
# fake fetchers (stand in for TieredFetcher)
# --------------------------------------------------------------------------- #

class FakeFetcher:
    """
    Serves a paginated storefront from an in-memory item list.

    `layout` is a list of pages; each page is a list of (id, title). Pages past
    the end return an empty grid, mirroring eBay.
    """

    def __init__(self, layouts, store_name="TestStore"):
        # layouts: {store_url: [ [items...], [items...] ] } or a bare list
        self.layouts = layouts
        self.store_name = store_name
        self.requests = []          # every URL fetched, in order
        self.closed = False

    def _layout_for(self, url):
        base = url.split("?")[0]
        if isinstance(self.layouts, dict):
            return self.layouts[base]
        return self.layouts

    def fetch(self, url):
        self.requests.append(url)
        pgn = int(parse_qs(urlparse(url).query).get("_pgn", ["1"])[0])
        layout = self._layout_for(url)
        if callable(layout):
            return layout(pgn), "http"
        items = layout[pgn - 1] if 1 <= pgn <= len(layout) else []
        return page(items, store=self.store_name), "http"

    def close(self):
        self.closed = True

    # convenience
    def count_for(self, base_url):
        return sum(1 for u in self.requests if u.split("?")[0] == base_url)


def paginate(items, per_page=200):
    return [items[i:i + per_page] for i in range(0, len(items), per_page)] or [[]]


def drifted_layout(items, per_page=200, *, missing=0, dupes=0, seed=0):
    """
    Model eBay's page shuffling: the whole catalogue is re-ordered, `missing`
    items fall out of the result set entirely for this run, and `dupes` items
    are served twice (on different pages), which is how items get pushed off
    the end of the last page in production.
    """
    rng = random.Random(seed)
    pool = list(items)
    rng.shuffle(pool)
    dropped = pool[:missing]
    kept = pool[missing:]
    kept = kept + rng.sample(kept, min(dupes, len(kept)))
    rng.shuffle(kept)
    return paginate(kept, per_page), {i for i, _ in dropped}
