"""Requirement 6: failure modes and hostile inputs."""
from __future__ import annotations

import json

import pytest

import scrape
from conftest import STORE_A, STORE_B, prod
from ebayfakes import FakeFetcher, card, iid, make_catalogue, page


# ------------------------------------------------------- corrupt snapshots --


def test_truncated_snapshot_json_is_treated_as_first_run(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", '{"store": "x", "seen": ["a", "b"], "produc')
    hz.use_products({"storea": [prod("a"), prod("z")]})
    assert hz.run() == 0
    assert hz.announced_ids() == [], "corrupt file must not cause a flood"
    assert set(hz.snapshot("storea")["seen"]) == {"a", "z"}


@pytest.mark.xfail(strict=True,
                   reason="DEFECT 6: scrape.py:275 discards a corrupt snapshot "
                          "wholesale, so the whole `seen` history is lost and any "
                          "id that is currently off-grid will be re-announced")
def test_truncated_snapshot_does_not_lose_the_seen_history(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", '{"store": "x", "seen": ["a", "b"], "produc')
    hz.use_products({"storea": [prod("a"), prod("z")]})
    hz.run()
    assert "b" in hz.snapshot("storea")["seen"]


def test_empty_snapshot_file(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", "")
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0
    assert hz.announced_ids() == []




def test_snapshot_that_is_a_json_list_is_survivable(hz):
    """Valid JSON of the wrong shape must not abort the run (was DEFECT 1)."""
    hz.set_stores([STORE_A, STORE_B])
    hz.write_snapshot("storea", "[]")
    hz.use_products({"storea": [prod("a")], "storeb": [prod("b")]})
    assert hz.run() == 0
    assert (hz.data_dir / "storeb.json").exists(), "the second store must still run"
    assert hz.announced_ids() == [], "a re-baselined store must not flood"




def test_snapshot_with_null_products_is_survivable(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {"store": "x", "products": None, "seen": ["a"]})
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0




def test_snapshot_with_string_products_is_survivable(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {"store": "x", "products": "oops"})
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0




def test_snapshot_products_missing_id_key_is_survivable(hz):
    """load_snapshot filters idless entries instead of raising KeyError."""
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {"store": "x", "seen": ["a"],
                                 "products": [{"title": "no id"}, prod("a")]})
    hz.use_products({"storea": [prod("a"), prod("b")]})
    assert hz.run() == 0
    assert hz.announced_ids() == ["b"]





def test_snapshot_with_only_seen_and_no_products_is_handled(hz):
    """A snapshot with no 'products' key at all (was REGRESSION B)."""
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {"store": "x", "seen": ["a", "b"]})
    hz.use_products({"storea": [prod("a"), prod("c")]})
    assert hz.run() == 0
    assert hz.announced_ids() == ["c"], "'b' is off-grid but still remembered"
    assert set(hz.snapshot("storea")["seen"]) == {"a", "b", "c"}


def test_load_snapshot_drops_a_non_list_seen(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", {"store": "x", "seen": "abc",
                                 "products": [prod("a")]})
    hz.use_products({"storea": [prod("a"), prod("b")]})
    assert hz.run() == 0
    assert hz.announced_ids() == ["b"]
    assert set(hz.snapshot("storea")["seen"]) == {"a", "b"}







def test_snapshot_with_json_number_at_top_level_is_survivable(hz):
    hz.set_stores([STORE_A])
    hz.write_snapshot("storea", "42")
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0


# ------------------------------------------------------------- env / args --

def test_missing_telegram_env_vars_exits_2(hz, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 2
    assert not hz.data_dir.exists(), "nothing is scraped without credentials"


def test_blank_telegram_token_exits_2(hz, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 2


def test_dry_run_works_without_credentials(hz, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    assert hz.run("--dry-run") == 0




def test_non_numeric_max_photos_falls_back_to_the_default(hz, monkeypatch):
    monkeypatch.setenv("MAX_PHOTOS", "all")
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0


# ----------------------------------------------------------- stores.json ---
#
# main() validates stores.json before it builds a fetcher: a bad config is a
# clean `return 2` with a message on stderr and zero HTTP work.


def assert_config_rejected(hz, capsys):
    """Exit 2, an explanatory line on stderr, and nothing scraped or written."""
    hz.use_no_fetcher()
    rc = hz.run()
    err = capsys.readouterr().err
    assert rc == 2, f"expected exit 2, got {rc}"
    assert "stores.json" in err, err
    assert not hz.data_dir.exists(), "no snapshot may be written for a bad config"
    return err


def test_malformed_stores_json_exits_2(hz, capsys):
    hz.set_stores_raw('[{"slug": "a", ')
    assert_config_rejected(hz, capsys)


def test_empty_stores_file_exits_2(hz, capsys):
    hz.set_stores_raw("")
    assert_config_rejected(hz, capsys)


def test_missing_stores_file_exits_2(hz, capsys):
    assert_config_rejected(hz, capsys)


def test_stores_json_as_an_object_instead_of_a_list_exits_2(hz, capsys):
    hz.set_stores({"slug": "a", "name": "A", "url": "https://e/str/a"})
    assert "must be a list" in assert_config_rejected(hz, capsys)


@pytest.mark.parametrize("payload", [42, "a string", None, True])
def test_stores_json_scalar_top_level_exits_2(hz, capsys, payload):
    hz.set_stores(payload)
    assert_config_rejected(hz, capsys)


def test_non_dict_entry_exits_2(hz, capsys):
    hz.set_stores([STORE_A, "herbaldirect"])
    assert "not a store object" in assert_config_rejected(hz, capsys)


def test_entry_that_is_a_list_exits_2(hz, capsys):
    hz.set_stores([["slug", "name", "url"]])
    assert_config_rejected(hz, capsys)


@pytest.mark.parametrize("key", ["slug", "name", "url"])
def test_missing_required_key_exits_2(hz, capsys, key):
    bad = {k: v for k, v in STORE_A.items() if k != key}
    hz.set_stores([bad, STORE_B])
    assert key in assert_config_rejected(hz, capsys)


@pytest.mark.parametrize("key", ["slug", "name", "url"])
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_required_value_exits_2(hz, capsys, key, value):
    hz.set_stores([dict(STORE_A, **{key: value})])
    assert key in assert_config_rejected(hz, capsys)


@pytest.mark.parametrize("key", ["slug", "name", "url"])
@pytest.mark.parametrize("value", [None, 7, [], {}, True])
def test_non_string_required_value_exits_2(hz, capsys, key, value):
    hz.set_stores([dict(STORE_A, **{key: value})])
    assert key in assert_config_rejected(hz, capsys)


@pytest.mark.parametrize("slug", ["a/b", "/abs", "../escape", "data/x", "x/"])
def test_slug_containing_a_slash_exits_2(hz, capsys, slug):
    hz.set_stores([dict(STORE_A, slug=slug)])
    assert "path-safe" in assert_config_rejected(hz, capsys)


def test_a_bad_second_entry_rejects_the_whole_config(hz, capsys):
    """Validation is all-or-nothing: the good first store must not be scraped."""
    hz.set_stores([STORE_A, dict(STORE_B, name="")])
    assert_config_rejected(hz, capsys)


def test_empty_stores_list_is_a_clean_noop(hz):
    hz.set_stores([])
    hz.use_fetcher(FakeFetcher([[]]))
    assert hz.run() == 0
    assert hz.fetchers[-1].requests == [], "no HTTP for an empty store list"
    assert hz.tg.calls == []


def test_a_valid_config_is_accepted_with_extra_keys(hz):
    hz.set_stores([dict(STORE_A, note="ignore me", enabled=True)])
    hz.use_products({"storea": [prod("a")]})
    assert hz.run() == 0


def test_the_real_stores_json_passes_validation(hz):
    """The config actually deployed must satisfy the new rules."""
    real = json.loads((scrape.ROOT / "stores.json").read_text(encoding="utf-8"))
    hz.set_stores(real)
    hz.use_products({s["slug"]: [prod("a")] for s in real})
    assert hz.run() == 0
    assert {s["slug"] for s in real} == {"herbaldirect", "supplementhealthshoppe"}


def test_config_errors_are_rejected_in_dry_run_too(hz, capsys):
    hz.set_stores_raw("{ nope")
    hz.use_no_fetcher()
    assert hz.run("--dry-run") == 2


def test_missing_credentials_is_checked_before_the_config(hz, capsys, monkeypatch):
    """Both are exit 2; the credential message must be the one shown."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    hz.set_stores_raw("{ nope")
    hz.use_no_fetcher()
    assert hz.run() == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


# ------------------------------------------------------- the `label` hoist --

def test_a_failure_line_names_the_store(hz, capsys):
    """L1: the except handlers use the hoisted label, not store['name']."""
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": scrape.ScrapeError("markup changed"),
                     "storeb": [prod("z")]})
    assert hz.run() == 1
    out = capsys.readouterr().out
    assert f"{STORE_A['name']}: markup changed" in out
    assert STORE_B["name"] not in out.split("Completed with problems:")[1]


def test_an_unexpected_failure_line_names_the_store_and_the_exception(hz, capsys):
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": RuntimeError("kaboom"), "storeb": [prod("z")]})
    assert hz.run() == 1
    out = capsys.readouterr().out
    assert f"{STORE_A['name']}: RuntimeError: kaboom" in out
    assert (hz.data_dir / "storeb.json").exists()


def test_the_banner_uses_the_label(hz, capsys):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()
    assert f"=== {STORE_A['name']} ===" in capsys.readouterr().out


def test_a_failure_before_slug_is_read_still_names_the_store(hz, capsys):
    """The label is computed above the inner try, so even a throw on the very
    first statement inside it is attributed."""
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": [prod("a")], "storeb": [prod("z")]})

    real = scrape.load_snapshot
    hz.mp.setattr(scrape, "load_snapshot",
                  lambda slug: (_ for _ in ()).throw(RuntimeError("boom"))
                  if slug == "storea" else real(slug))
    assert hz.run() == 1
    assert f"{STORE_A['name']}: RuntimeError: boom" in capsys.readouterr().out


# --------------------------------------------------------------- unicode ---

def test_unicode_titles_round_trip_through_the_snapshot(hz):
    hz.set_stores([STORE_A])
    titles = ["Ashwagandha KSM-66 – 600µg", "Café Vert 🌿 Extract",
              "ビタミンC 1000mg", "Ω-3 Fish Oil «Premium»"]
    ps = [prod(f"90{i}", t) for i, t in enumerate(titles)]
    hz.use_products({"storea": ps[:1]})
    hz.run()
    hz.use_products({"storea": ps})
    hz.run()
    assert sorted(hz.announced_ids()) == ["901", "902", "903"]
    raw = (hz.data_dir / "storea.json").read_text(encoding="utf-8")
    assert "🌿" in raw and "ビタミンC" in raw
    assert "ビタミンC 1000mg" in hz.tg.blob()


def test_unicode_titles_survive_the_parser_and_scrape_store(hz):
    hz.set_stores([STORE_A])
    hz.use_fetcher(FakeFetcher({STORE_A["url"]: [[(iid(911), "Café 🌿 Ω")]]}))
    assert hz.run() == 0
    assert hz.snapshot("storea")["products"][0]["title"] == "Café 🌿 Ω"


# ------------------------------------------------- suspicious-drop guard ----

def test_small_store_is_not_protected_by_the_drop_guard(hz):
    """The guard only arms at >=20 previous items."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(f"{i}") for i in range(19)]})
    hz.run()
    hz.use_products({"storea": [prod("0")]})
    assert hz.run() == 0
    assert hz.snapshot("storea")["count"] == 1


def test_drop_guard_boundary_exactly_half(hz):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(100)]})
    hz.run()
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(50)]})
    assert hz.run() == 0, "exactly 50% is allowed through"
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(24)]})
    assert hz.run() == 1, "below 50% of the *previous* count is blocked"


def test_store_dropping_to_zero_is_blocked(hz):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(100)]})
    hz.run()
    hz.use_products({"storea": []})
    assert hz.run() == 1
    assert hz.snapshot("storea")["count"] == 100


def test_repeated_suspicious_drops_never_self_heal(hz):
    """The guard compares against the frozen snapshot, so a store that really
    did shrink by >50% stays blocked forever until someone runs --reset."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(100)]})
    hz.run()
    hz.use_products({"storea": [prod(f"{i:03d}") for i in range(30)]})
    for _ in range(4):
        assert hz.run() == 1
    assert hz.snapshot("storea")["count"] == 100


# ------------------------------------------------------------ ordering -----





def test_a_crash_during_announce_does_not_abort_the_whole_run(hz):
    """The whole per-store body is inside the try now (was REGRESSION C)."""
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": [prod("a")], "storeb": [prod("z")]})
    hz.run()

    spy = scrape.announce_new

    def boom_for_store_a(tg, store, new, mp):
        if store["slug"] == "storea":
            raise RuntimeError("Telegram library blew up mid-announce")
        return spy(tg, store, new, mp)

    hz.mp.setattr(scrape, "announce_new", boom_for_store_a)
    hz.use_products({"storea": [prod("a"), prod("b")],
                     "storeb": [prod("z"), prod("y")]})
    assert hz.run() == 1
    assert "b" not in hz.snapshot("storea")["seen"], "store A rolled back"
    assert "y" in hz.snapshot("storeb")["seen"], "store B still ran"


def test_a_crash_in_save_snapshot_is_contained(hz, monkeypatch):
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": [prod("a")], "storeb": [prod("z")]})
    hz.run()

    real_save = scrape.save_snapshot

    def boom(slug, *a, **k):
        if slug == "storea":
            raise OSError("disk full")
        return real_save(slug, *a, **k)

    monkeypatch.setattr(scrape, "save_snapshot", boom)
    hz.use_products({"storea": [prod("a"), prod("b")],
                     "storeb": [prod("z"), prod("y")]})
    assert hz.run() == 1
    assert "y" in hz.snapshot("storeb")["seen"]



def test_alerts_are_retried_after_a_crash_during_announce(hz):
    """announce_new runs BEFORE save_snapshot, so a crash mid-announce leaves
    the ids unremembered and they are retried (was DEFECT 5)."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()

    spy = scrape.announce_new

    def boom(*_a, **_k):
        raise RuntimeError("runner killed mid-announce")

    scrape.announce_new = boom
    try:
        hz.use_products({"storea": [prod("a"), prod("b")]})
        assert hz.run() == 1, "reported as a failure, not silently ok"
    finally:
        scrape.announce_new = spy
    assert "b" not in hz.snapshot("storea")["seen"]

    hz.use_products({"storea": [prod("a"), prod("b")]})
    assert hz.run() == 0
    assert hz.announced_ids() == ["b"], "product b is retried on the next run"


def test_a_telegram_failure_leaves_the_id_unremembered_and_retries(hz):
    """The whole point of announce_new returning failed ids."""
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()

    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    hz.mp.setattr(scrape, "announce_new",
                  lambda tg, store, new, mp: [p["id"] for p in new])
    assert hz.run() == 1, "undelivered alerts are a run failure"
    snap = hz.snapshot("storea")
    assert "b" not in snap["seen"] and "c" not in snap["seen"]
    assert {p["id"] for p in snap["products"]} == {"a", "b", "c"}, \
        "the products list is still updated"

    # next run: Telegram is healthy again, both are announced
    hz.mp.undo()
    hz2 = type(hz)(hz.tmp, hz.mp)
    hz2.set_stores([STORE_A])
    hz2.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    assert hz2.run() == 0
    assert sorted(hz2.announced_ids()) == ["b", "c"]


def test_a_partial_telegram_failure_only_retries_the_failed_ids(hz):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()
    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    hz.mp.setattr(scrape, "announce_new", lambda tg, store, new, mp: ["c"])
    assert hz.run() == 1
    seen = set(hz.snapshot("storea")["seen"])
    assert "b" in seen and "c" not in seen

# ------------------------------------------------------------------------- #
# Adversarial sweep of the re-indented per-store loop body
# ------------------------------------------------------------------------- #

def test_continue_in_the_baseline_path_moves_to_the_next_store(hz):
    """The three `continue`s are now inside a try; they must still advance the
    for-loop, not fall through into the diff below."""
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": [prod("a")], "storeb": [prod("z")]})
    assert hz.run() == 0
    for slug in ("storea", "storeb"):
        assert hz.snapshot(slug)["count"] == 1
    assert hz.tg.methods() == ["sendMessage", "sendMessage"]   # two baselines
    assert hz.announced_ids() == []


def test_continue_in_the_drop_guard_path_moves_to_the_next_store(hz):
    hz.set_stores([STORE_A, STORE_B])
    big = [prod(f"{i:03d}") for i in range(100)]
    hz.use_products({"storea": big, "storeb": big})
    hz.run()
    hz.use_products({"storea": big[:10], "storeb": big + [prod("new")]})
    assert hz.run() == 1
    assert hz.snapshot("storea")["count"] == 100, "store A not rewritten"
    assert hz.announced_ids("storeb") == ["new"], "store B still diffed normally"


def test_reset_path_still_works_inside_the_try(hz):
    hz.set_stores([STORE_A, STORE_B])
    hz.write_snapshot("storea", {"store": "x", "url": "y", "count": 2,
                                 "seen": ["s1", "old-only"],
                                 "products": [prod("s1"), prod("p-only")]})
    hz.use_products({"storea": [prod("s2")], "storeb": [prod("z")]})
    assert hz.run("--reset") == 0
    assert hz.announced_ids() == []
    assert set(hz.snapshot("storea")["seen"]) == {"s1", "s2", "old-only", "p-only"}
    assert set(hz.snapshot("storeb")["seen"]) == {"z"}


def test_reset_after_a_failing_store_still_resets_the_other(hz):
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": scrape.ScrapeError("markup changed"),
                     "storeb": [prod("z")]})
    assert hz.run("--reset") == 1
    assert not (hz.data_dir / "storea.json").exists()
    assert set(hz.snapshot("storeb")["seen"]) == {"z"}


def test_slug_is_not_leaked_between_stores_after_a_failure(hz):
    """`slug` is assigned inside the try; a failure must not make the next
    store write to the previous store's file."""
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": scrape.ScrapeError("boom"), "storeb": [prod("z")]})
    assert hz.run() == 1
    assert not (hz.data_dir / "storea.json").exists()
    assert hz.snapshot("storeb")["store"] == STORE_B["name"]


def test_total_new_is_reported_and_exit_code_is_zero_on_a_clean_run(hz, capsys):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()
    hz.use_products({"storea": [prod("a"), prod("b"), prod("c")]})
    assert hz.run() == 0
    assert "Done. 2 new product(s)" in capsys.readouterr().out


def test_a_failure_in_the_first_store_does_not_suppress_the_second_alert(hz):
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": [prod("a")], "storeb": [prod("z")]})
    hz.run()
    hz.use_products({"storea": scrape.ScrapeError("blocked"),
                     "storeb": [prod("z"), prod("y")]})
    assert hz.run() == 1
    assert hz.announced_ids() == ["y"]


def test_fetcher_is_closed_even_when_every_store_fails(hz):
    from ebayfakes import FakeFetcher
    hz.set_stores([STORE_A, STORE_B])
    f = FakeFetcher({STORE_A["url"]: [[]], STORE_B["url"]: [[]]})
    hz.use_fetcher(f)
    assert hz.run() == 1
    assert f.closed, "TieredFetcher.close() must still run in the finally block"


def test_surge_and_drop_guards_do_not_both_fire(hz):
    """A store that halves is blocked by the drop guard before the surge
    guard can see the (necessarily small) new-item list."""
    hz.set_stores([STORE_A])
    base = [prod(f"{i:04d}") for i in range(1000)]
    hz.use_products({"storea": base})
    hz.run()
    hz.use_products({"storea": base[:100] + [prod(f"n{i}") for i in range(300)]})
    assert hz.run() == 1
    assert hz.announced_ids() == []
    assert "only 400 products" in hz.tg.texts()[0], "the drop guard message, not surge"


# ------------------------------------------------------------------------- #
# Adversarial sweep: stores.json validation + the label hoist
# ------------------------------------------------------------------------- #

def test_duplicate_slugs_are_not_rejected_and_share_one_snapshot_file(hz):
    """
    NOT COVERED by the new validation: two entries may share a slug, so they
    share one snapshot file.
    """
    hz.set_stores([dict(STORE_A, slug="same"), dict(STORE_B, slug="same")])
    hz.use_products({"same": [prod("a1"), prod("a2")]})
    assert hz.run() == 0
    assert [p.name for p in sorted(hz.data_dir.iterdir())] == ["same.json"], \
        "two stores, one snapshot file"
    assert hz.snapshot("same")["store"] == STORE_B["name"], "the last store wins"


def test_duplicate_slugs_cause_a_permanent_repeating_flood(hz):
    """Each run, store B's snapshot erases store A's `seen` and vice versa, so
    both catalogues are announced as new on every single run, for ever."""
    hz.set_stores([dict(STORE_A, slug="same"), dict(STORE_B, slug="same")])

    def fake_scrape_store(fetcher, store):
        which = ["a1", "a2"] if store["name"] == STORE_A["name"] else ["b1", "b2"]
        return sorted((prod(i) for i in which), key=lambda p: p["id"])

    hz.mp.setattr(scrape, "scrape_store", fake_scrape_store)
    hz.use_fetcher(FakeFetcher([[]]))
    hz.run()                                   # baselines

    counts = []
    for _ in range(3):
        hz.run()
        counts.append(len(hz.announced_ids()))
    assert counts == [2, 2, 2], \
        f"the same 2 products are re-announced every run for ever: {counts}"


def test_slug_of_dots_is_accepted(hz):
    """`..` passes validation; on POSIX it cannot escape data/, but it does
    produce a surprising filename."""
    hz.set_stores([dict(STORE_A, slug="..")])
    hz.use_products({"..": [prod("a")]})
    assert hz.run() == 0
    assert (hz.data_dir / "...json").exists()


def test_backslash_in_a_slug_is_accepted(hz):
    hz.set_stores([dict(STORE_A, slug="a\\b")])
    hz.use_products({"a\\b": [prod("a")]})
    assert hz.run() == 0
    assert (hz.data_dir / "a\\b.json").exists()


def test_whitespace_padded_values_pass_validation_unstripped(hz):
    """Validation tests `.strip()` for emptiness but keeps the padded value, so
    a padded slug becomes a padded filename and a padded url a broken request."""
    hz.set_stores([dict(STORE_A, slug=" storea ")])
    hz.use_products({" storea ": [prod("a")]})
    assert hz.run() == 0
    assert (hz.data_dir / " storea .json").exists()


def test_validation_happens_before_the_fetcher_is_built(hz):
    """Ordering matters: a config error must not launch Playwright."""
    hz.set_stores([dict(STORE_A, url="")])
    hz.use_no_fetcher()
    assert hz.run("--force-browser") == 2


def test_label_survives_a_name_that_is_only_punctuation(hz, capsys):
    hz.set_stores([dict(STORE_A, name="<&>")])
    hz.use_products({"storea": RuntimeError("kaboom")})
    assert hz.run() == 1
    assert "<&>: RuntimeError: kaboom" in capsys.readouterr().out


def test_label_is_recomputed_per_store(hz, capsys):
    hz.set_stores([STORE_A, STORE_B])
    hz.use_products({"storea": RuntimeError("first"),
                     "storeb": RuntimeError("second")})
    assert hz.run() == 1
    out = capsys.readouterr().out
    assert f"{STORE_A['name']}: RuntimeError: first" in out
    assert f"{STORE_B['name']}: RuntimeError: second" in out


def test_a_store_whose_telegram_send_fails_still_names_itself(hz, capsys):
    hz.set_stores([STORE_A])
    hz.use_products({"storea": [prod("a")]})
    hz.run()
    hz.tg_fail_methods = {"sendMessage", "sendPhoto", "sendMediaGroup"}
    hz.use_products({"storea": [prod("a"), prod("b")]})
    assert hz.run() == 1
    assert f"{STORE_A['name']}: 1 alert(s) not delivered" in capsys.readouterr().out
