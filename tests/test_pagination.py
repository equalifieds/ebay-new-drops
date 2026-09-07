"""Requirement 4: pagination logic in scrape_store (the real function)."""
from __future__ import annotations

import pytest

import scrape
from scrape import MAX_PAGES, FULL_PAGE_THRESHOLD, ScrapeError, scrape_store
from conftest import STORE_A
from ebayfakes import SOLD_OUT_HTML, card, iid, make_catalogue, page, paginate
from ebayfakes import FakeFetcher

LEAN = ('<article class="str-item-card" data-testid="ig-{i}">'
        '<a class="str-item-card__link" aria-label="P{i}" href="/itm/{i}"></a>'
        '</article>')


def lean(n):
    """A minimal card with a valid 12-digit id."""
    return LEAN.format(i=iid(n))


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda *_a, **_k: None)


def lean_page(start, n):
    return "<html><body>" + "".join(lean(start + k) for k in range(n)) + "</body></html>"


def test_stops_on_a_short_page():
    items = make_catalogue(450)
    f = FakeFetcher(paginate(items, 200))
    got = scrape_store(f, STORE_A)
    assert len(got) == 450
    assert len(f.requests) == 3          # 200 + 200 + 50(short) -> stop
    assert "_pgn=3" in f.requests[-1]


def test_exactly_full_page_forces_another_fetch():
    items = make_catalogue(400)
    f = FakeFetcher(paginate(items, 200))
    got = scrape_store(f, STORE_A)
    assert len(got) == 400
    assert len(f.requests) == 3, "200,200 then an empty page 3 to confirm the end"


def test_page_size_threshold_boundary():
    assert FULL_PAGE_THRESHOLD == 200
    f = FakeFetcher([make_catalogue(199)])
    assert len(scrape_store(f, STORE_A)) == 199
    assert len(f.requests) == 1, "199 < 200 -> last page, no second fetch"


def test_max_pages_ceiling_is_enforced():
    def layout(pgn):
        return lean_page((pgn - 1) * 200, 200)      # infinite full pages
    f = FakeFetcher(layout)
    got = scrape_store(f, STORE_A)
    assert len(f.requests) == MAX_PAGES == 80
    assert len(got) == 80 * 200
    # NOTE: hitting the ceiling is silent -- no exception, no warning.




def test_one_repeated_page_does_not_truncate_the_scrape():
    """eBay re-serves a page while shuffling; one repeat must not end the walk."""
    p1 = make_catalogue(200)
    f = FakeFetcher([p1, list(p1), make_catalogue(200, start=200), []])
    got = scrape_store(f, STORE_A)
    assert len(got) == 400, "page 3 held 200 further products"
    assert len(f.requests) == 4


def test_two_consecutive_repeated_pages_end_the_walk():
    p1 = make_catalogue(200)
    f = FakeFetcher([p1, list(p1), list(p1), make_catalogue(200, start=200)])
    got = scrape_store(f, STORE_A)
    assert len(got) == 200
    assert len(f.requests) == 3, "stopped after the second repeat in a row"


def test_the_repeat_counter_resets_after_a_productive_page():
    """repeat, new, repeat, new, ... must never trip the 2-in-a-row rule."""
    a = make_catalogue(200)
    b = make_catalogue(200, start=200)
    c = make_catalogue(200, start=400)
    f = FakeFetcher([a, list(a), b, list(b), c, []])
    got = scrape_store(f, STORE_A)
    assert len(got) == 600
    assert len(f.requests) == 6


def test_partial_overlap_page_does_not_stop():
    p1 = make_catalogue(200)
    p2 = p1[100:] + make_catalogue(100, start=200)
    f = FakeFetcher([p1, p2, []])
    got = scrape_store(f, STORE_A)
    assert len(got) == 300


def test_duplicate_ids_are_deduplicated_first_wins():
    dup = [(iid(111), "First Title"), (iid(111), "Second Title"), (iid(222), "Other")]
    f = FakeFetcher([dup])
    got = scrape_store(f, STORE_A)
    assert [p["id"] for p in got] == [iid(111), iid(222)]
    assert got[0]["title"] == "First Title"


def test_results_are_sorted_by_id():
    f = FakeFetcher([[(iid(333), "c"), (iid(111), "a"), (iid(222), "b")]])
    assert [p["id"] for p in scrape_store(f, STORE_A)] == [iid(111), iid(222), iid(333)]


def test_sold_out_store_returns_empty_without_error():
    f = FakeFetcher(lambda pgn: SOLD_OUT_HTML)
    assert scrape_store(f, STORE_A) == []
    assert len(f.requests) == 1


def test_sold_out_marker_with_items_present_does_not_stop_the_scrape():
    def layout(pgn):
        if pgn == 1:
            return page(make_catalogue(200), banner="We are sold out of gift cards")
        if pgn == 2:
            return page(make_catalogue(10, start=200))
        return page([])
    f = FakeFetcher(layout)
    assert len(scrape_store(f, STORE_A)) == 210


def test_page_one_with_zero_cards_raises_loudly():
    f = FakeFetcher([[]])
    with pytest.raises(ScrapeError) as e:
        scrape_store(f, STORE_A)
    assert "markup has probably changed" in str(e.value)


def test_page_one_with_html_but_no_cards_raises():
    f = FakeFetcher(lambda pgn: "<html><body><div>store header</div></body></html>")
    with pytest.raises(ScrapeError):
        scrape_store(f, STORE_A)


def test_empty_page_after_page_one_just_ends_the_walk():
    f = FakeFetcher([make_catalogue(200), []])
    assert len(scrape_store(f, STORE_A)) == 200
    assert len(f.requests) == 2


def test_scrape_error_on_one_store_does_not_stop_the_other(hz):
    from conftest import STORE_B, prod
    hz.set_stores([STORE_A, STORE_B])
    hz.use_fetcher(FakeFetcher({
        STORE_A["url"]: [[]],                                     # broken markup
        STORE_B["url"]: [make_catalogue(5)],
    }))
    rc = hz.run()
    assert rc == 1
    assert (hz.data_dir / "storeb.json").exists()
    assert not (hz.data_dir / "storea.json").exists()



def test_url_construction():
    assert scrape.store_page_url("https://www.ebay.com/str/x", 4) == \
        "https://www.ebay.com/str/x?_ipg=200&_pgn=4"


def test_requested_page_size_matches_the_full_page_threshold():
    """_ipg must equal what the code treats as a full page, or the walk either
    stops early or strides past listings it never fetches."""
    assert scrape.ITEMS_PER_PAGE == scrape.FULL_PAGE_THRESHOLD == 200