"""
Requirement 1: the duplicate-alert fix.

These drive the REAL scrape.main() diff logic and the REAL
save_snapshot/load_snapshot. Only the network layer is faked.
"""
from __future__ import annotations

import json

import pytest

import scrape
from scrape import SURGE_LIMIT
from conftest import STORE_A, prod


def ids(n, start=0):
    return [f"1550{i:08d}" for i in range(start, start + n)]


# --------------------------------------------------------------- (a) + (b) --

def test_disappearing_product_is_not_reannounced(hz):
    hz.set_stores([STORE_A])
    a, b, c = ids(3)

    hz.use_products({"storea": [prod(a), prod(b), prod(c)]})
    assert hz.run() == 0
    assert hz.announced_ids() == []                       # first run = baseline

    # b drops out of the scrape (eBay shuffled it off the last page)
    hz.use_products({"storea": [prod(a), prod(c)]})
    assert hz.run() == 0
    assert hz.announced_ids() == []

    # ...and comes back
    hz.use_products({"storea": [prod(a), prod(b), prod(c)]})
    assert hz.run() == 0
    assert hz.announced_ids() == [], "returning product must not be re-announced"


def test_genuinely_new_id_is_announced_exactly_once(hz):
    hz.set_stores([STORE_A])
    a, b, c, d = ids(4)

    hz.use_products({"storea": [prod(a), prod(b), prod(c)]})
    hz.run()

    hz.use_products({"storea": [prod(a), prod(b), prod(c), prod(d)]})
    hz.run()
    assert hz.announced_ids() == [d]
    assert any("1 new product" in t for t in hz.tg.texts())

    hz.run()                                              # identical scrape again
    assert hz.announced_ids() == []

    # d disappears then returns -> still silent
    hz.use_products({"storea": [prod(a), prod(b), prod(c)]})
    hz.run()
    hz.use_products({"storea": [prod(a), prod(b), prod(c), prod(d)]})
    hz.run()
    assert hz.announced_ids() == []


# ------------------------------------------------------------------- (c) ---

def test_seen_grows_monotonically_and_is_never_lost(hz):
    hz.set_stores([STORE_A])
    all_ids = ids(10)
    history = set()

    import random
    rng = random.Random(7)
    for run_no in range(8):
        # each run sees a random 60% subset, plus one brand-new id after run 3
        subset = rng.sample(all_ids, 6)
        if run_no == 4:
            subset = subset + [ "9999" ]
        hz.use_products({"storea": [prod(i) for i in subset]})
        hz.run()
        snap = hz.snapshot("storea")
        seen = set(snap["seen"])
        assert history <= seen, f"run {run_no}: seen shrank, lost {history - seen}"
        assert set(snap["seen"]) == seen
        assert sorted(snap["seen"]) == snap["seen"], "seen must be stored sorted"
        history = seen
        # products always reflects only the current scrape
        assert {p["id"] for p in snap["products"]} == set(subset)
        assert snap["count"] == len(subset)

    assert "9999" in history


# ------------------------------------------------------------------- (d) ---

def test_old_format_snapshot_migrates_without_flooding(hz):
    """A snapshot written by the OLD code has 'products' but no 'seen'."""
    hz.set_stores([STORE_A])
    catalogue = ids(300)
    hz.write_snapshot("storea", {
        "store": STORE_A["name"], "url": STORE_A["url"],
        "scraped_at": "2026-09-01T00:00:00+00:00",
        "count": 300,
        "products": [prod(i) for i in catalogue],
    })
    assert "seen" not in hz.snapshot("storea")

    # exactly the same catalogue comes back
    hz.use_products({"storea": [prod(i) for i in catalogue]})
    assert hz.run() == 0
    assert hz.announced_ids() == [], "migration must not announce the whole catalogue"
    snap = hz.snapshot("storea")
    assert set(snap["seen"]) == set(catalogue)

    # 30 shuffle out on the next run, then return: still silent
    hz.use_products({"storea": [prod(i) for i in catalogue[:270]]})
    hz.run()
    assert hz.announced_ids() == []
    hz.use_products({"storea": [prod(i) for i in catalogue]})
    hz.run()
    assert hz.announced_ids() == []


def test_old_format_snapshot_announces_only_truly_new(hz):
    hz.set_stores([STORE_A])
    old = ids(50)
    hz.write_snapshot("storea", {"store": "x", "url": "y", "count": 50,
                                 "products": [prod(i) for i in old]})
    fresh = ids(3, start=900)
    hz.use_products({"storea": [prod(i) for i in old + fresh]})
    hz.run()
    assert sorted(hz.announced_ids()) == sorted(fresh)


# ------------------------------------------------------------------- (e) ---

def test_reset_seeds_union_and_announces_nothing(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {
        "store": "x", "url": "y", "count": 3,
        "seen": ["s1", "s2", "old-only"],
        "products": [prod("s1"), prod("s2"), prod("p-only")],
    })
    hz.use_products({"storea": [prod("s2"), prod("brand-new")]})

    assert hz.run("--reset") == 0
    assert hz.announced_ids() == []
    snap = hz.snapshot("storea")
    assert set(snap["seen"]) == {"s1", "s2", "old-only", "p-only", "brand-new"}
    assert {p["id"] for p in snap["products"]} == {"s2", "brand-new"}

    # and the reset must not have armed a later flood
    hz.use_products({"storea": [prod("s1"), prod("s2"), prod("p-only"),
                                prod("old-only"), prod("brand-new")]})
    hz.run()
    assert hz.announced_ids() == []


def test_reset_with_no_previous_snapshot(hz):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(i) for i in ids(5)]})
    assert hz.run("--reset") == 0
    assert hz.announced_ids() == []
    assert set(hz.snapshot("storea")["seen"]) == set(ids(5))


def test_baseline_message_is_sent_on_first_run_but_no_product_alerts(hz):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(i) for i in ids(5)]})
    hz.run()
    assert hz.tg.methods() == ["sendMessage"]
    assert "Baseline saved" in hz.tg.texts()[0]
    assert hz.announced_ids() == []


def test_dry_run_still_writes_snapshot(hz):
    """--dry-run is documented as 'scrape + diff, send nothing'."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(i) for i in ids(3)]})
    hz.run("--dry-run")
    snap = hz.snapshot("storea")
    assert set(snap["seen"]) == set(ids(3))


# ------------------------------------------------- consequences of a lost push

def test_lost_push_causes_duplicate_alerts_next_run(hz):
    """
    watch.yml can fail to push the snapshot (conflicting rebase -> `exit 1`).
    The next run then diffs against the *previous* commit, so everything the
    lost run found is announced a second time.
    """
    hz.set_stores([STORE_A])
    base = ids(5)
    hz.use_products({"storea": [prod(i) for i in base]})
    hz.run()
    committed = (hz.data_dir / "storea.json").read_text(encoding="utf-8")

    fresh = ids(2, start=800)
    hz.use_products({"storea": [prod(i) for i in base + fresh]})
    hz.run()
    assert sorted(hz.announced_ids()) == sorted(fresh)

    # the push was rejected: the runner is ephemeral, so the next checkout gets
    # the older file back
    hz.write_snapshot("storea", committed)

    hz.use_products({"storea": [prod(i) for i in base + fresh]})
    hz.run()
    assert sorted(hz.announced_ids()) == sorted(fresh), \
        "same two products announced again after the lost push"


# --------------------------------------------- first run against a dead store




def test_first_run_while_the_store_is_sold_out_does_not_arm_a_flood(hz):
    """A zero baseline must not turn the whole catalogue into alerts (DEFECT 10)."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": []})
    assert hz.run() == 0
    assert hz.snapshot("storea")["seen"] == []

    catalogue = ids(500)
    hz.use_products({"storea": [prod(i) for i in catalogue]})
    assert hz.run() == 1
    assert hz.announced_ids() == []
    assert hz.tg.methods() == ["sendMessage"], "one warning, not 500 alerts"
    assert set(hz.snapshot("storea")["seen"]) == set(catalogue)

    hz.use_products({"storea": [prod(i) for i in catalogue]})
    assert hz.run() == 0
    assert hz.tg.calls == []



def test_snapshot_growth_and_seen_retention(hz):
    """`seen` is never pruned; size the file for the real VitaminRush store."""
    hz.set_stores([STORE_A])
    catalogue = ids(9400)
    hz.use_products({"storea": [prod(i) for i in catalogue]})
    hz.run()
    size = (hz.data_dir / "storea.json").stat().st_size
    print(f"\n  snapshot for 9,400 products: {size/1024/1024:.2f} MiB; "
          f"rewritten up to 12 times/day")

    # a year of turnover keeps accumulating in `seen`; 4,000 at once trips the
    # surge guard, so it is recorded but not alerted one-by-one
    turned_over = ids(4000, start=100000)
    hz.use_products({"storea": [prod(i) for i in catalogue[4000:] + turned_over]})
    assert hz.run() == 1
    assert hz.announced_ids() == []
    assert len(hz.snapshot("storea")["seen"]) == 13400


# --------------------------------------------------------------- surge guard

def test_surge_guard_replaces_a_flood_with_one_warning(hz):
    hz.set_stores([STORE_A])
    base = ids(800)
    hz.use_products({"storea": [prod(i) for i in base]})
    hz.run()

    surge = ids(400, start=500000)          # > max(SURGE_LIMIT, 800//4 = 200)
    hz.use_products({"storea": [prod(i) for i in base + surge]})
    assert hz.run() == 1, "a surge is reported as a run problem"
    assert hz.announced_ids() == [], "no per-product alerts"
    assert hz.tg.methods() == ["sendMessage"], "exactly one warning message"
    assert "400 listings" in hz.tg.texts()[0]

    # the ids are still remembered, so the next run is quiet
    hz.use_products({"storea": [prod(i) for i in base + surge]})
    assert hz.run() == 0
    assert hz.announced_ids() == []
    assert hz.tg.calls == []


def test_surge_threshold_boundary(hz):
    """> max(SURGE_LIMIT, len(seen)//4), so exactly at the limit still alerts."""
    hz.set_stores([STORE_A])
    base = ids(400)                          # 400//4 = 100 -> threshold 200
    hz.use_products({"storea": [prod(i) for i in base]})
    hz.run()

    at_limit = ids(SURGE_LIMIT, start=600000)
    hz.use_products({"storea": [prod(i) for i in base + at_limit]})
    assert hz.run() == 0
    assert len(hz.announced_ids()) == SURGE_LIMIT

    over = ids(SURGE_LIMIT + 1, start=700000)
    hz.use_products({"storea": [prod(i) for i in base + at_limit + over]})
    assert hz.run() == 1
    assert hz.announced_ids() == []



def test_surge_threshold_is_capped_at_500(hz):
    """min(max(SURGE_LIMIT, len(seen)//4), 500): even a 9,400-item store can
    never fire more than 500 individual alerts in one run."""
    hz.set_stores([STORE_A])
    base = ids(9400)
    hz.use_products({"storea": [prod(i) for i in base]})
    hz.run()

    ok_restock = ids(500, start=800000)
    hz.use_products({"storea": [prod(i) for i in base + ok_restock]})
    assert hz.run() == 0
    assert len(hz.announced_ids()) == 500, "exactly at the cap still alerts"

    over = ids(501, start=810000)
    hz.use_products({"storea": [prod(i) for i in base + ok_restock + over]})
    assert hz.run() == 1
    assert hz.announced_ids() == []
    assert hz.tg.methods() == ["sendMessage"]


def test_a_normal_restock_is_not_suppressed(hz):
    hz.set_stores([STORE_A])
    base = ids(576)
    hz.use_products({"storea": [prod(i) for i in base]})
    hz.run()
    hz.use_products({"storea": [prod(i) for i in base + ids(12, start=900000)]})
    assert hz.run() == 0
    assert len(hz.announced_ids()) == 12