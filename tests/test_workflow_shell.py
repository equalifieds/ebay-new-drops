"""
Review of .github/workflows/watch.yml -- the commit/push retry block is
extracted verbatim from the YAML and executed under `bash -e`, which is the
shell GitHub Actions uses for a `run:` step on Linux.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "watch.yml"


def extract_block(step_name):
    """Pull the `run:` script of a named step out of the workflow YAML."""
    text = WORKFLOW.read_text()
    m = re.search(rf"- name: {re.escape(step_name)}.*?run: \|\n(.*?)(?=\n      - |\Z)",
                  text, re.S)
    assert m, f"step {step_name!r} not found"
    return textwrap.dedent(m.group(1)).rstrip() + "\n"


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=check)


@pytest.fixture
def repos(tmp_path):
    """origin.git + a 'runner' clone + an 'other' clone racing against it."""
    if shutil.which("git") is None:
        pytest.skip("git not available")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=origin, check=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    for k, v in (("user.email", "a@b"), ("user.name", "seed"),
                 ("push.negotiate", "false")):
        git(seed, "config", k, v)
    (seed / "data").mkdir()
    (seed / "data" / "storea.json").write_text('{"seen": ["a"]}\n')
    (seed / "README").write_text("x\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "init")
    git(seed, "remote", "add", "origin", str(origin))
    git(seed, "push", "-q", "origin", "main")

    clones = {}
    for name in ("runner", "other"):
        p = tmp_path / name
        subprocess.run(["git", "clone", "-q", str(origin), str(p)], check=True)
        for k, v in (("user.email", "a@b"), ("user.name", name),
                     ("push.negotiate", "false")):
            git(p, "config", k, v)
        clones[name] = p
    clones["origin"] = origin
    return clones


def run_block(cwd, block=None):
    script = block if block is not None else extract_block("Commit updated snapshots")
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # GitHub Actions runs `run:` as `bash -e {0}` on Linux.
    return subprocess.run(["bash", "-e", "-c", script], cwd=cwd,
                          capture_output=True, text=True, env=env)


# --------------------------------------------------------------------------- #

def test_commit_message_date_substitution_is_correctly_quoted():
    block = extract_block("Commit updated snapshots")
    assert 'date -u +%Y-%m-%d\\ %H:%M' in block
    out = subprocess.run(
        ["bash", "-c", 'echo "Update product snapshots '
                       '($(date -u +%Y-%m-%d\\ %H:%M) UTC)"'],
        capture_output=True, text=True, check=True).stdout.strip()
    assert re.fullmatch(r"Update product snapshots \(\d{4}-\d{2}-\d{2} "
                        r"\d{2}:\d{2} UTC\)", out), out


def test_no_changes_exits_zero(repos):
    r = run_block(repos["runner"])
    assert r.returncode == 0
    assert "No snapshot changes to commit." in r.stdout


def test_clean_push_succeeds_on_first_attempt(repos):
    (repos["runner"] / "data" / "storea.json").write_text('{"seen": ["a","b"]}\n')
    r = run_block(repos["runner"])
    assert r.returncode == 0, r.stderr
    assert "Pushed on attempt 1." in r.stdout


def test_lost_race_on_a_different_file_rebases_and_retries(repos):
    other = repos["other"]
    (other / "README").write_text("someone else\n")
    git(other, "commit", "-qam", "unrelated")
    git(other, "push", "-q", "origin", "main")

    (repos["runner"] / "data" / "storea.json").write_text('{"seen": ["a","b"]}\n')
    r = run_block(repos["runner"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Push rejected" in r.stdout
    assert "Pushed on attempt 2." in r.stdout
    log = git(repos["origin"], "log", "--oneline", "main").stdout
    assert "Update product snapshots" in log and "unrelated" in log




def test_conflicting_race_on_the_same_snapshot_still_lands(repos):
    """Our snapshot is the newer read of derived data, so it must win a
    conflicting race rather than being thrown away (was DEFECT 8)."""
    other = repos["other"]
    (other / "data" / "storea.json").write_text('{"seen": ["a","OTHER"]}\n')
    git(other, "commit", "-qam", "other snapshot")
    git(other, "push", "-q", "origin", "main")

    runner = repos["runner"]
    (runner / "data" / "storea.json").write_text('{"seen": ["a","MINE"]}\n')
    r = run_block(runner)
    assert r.returncode == 0, r.stdout + r.stderr
    log = git(repos["origin"], "log", "--oneline", "main").stdout
    assert "Update product snapshots" in log
    assert "other snapshot" in log, "the other run's commit must not be erased"
    # our snapshot content is what landed
    blob = git(repos["origin"], "show", "main:data/storea.json").stdout
    assert "MINE" in blob
    # and no rebase is left half-finished
    assert not (runner / ".git" / "rebase-merge").exists()
    assert not (runner / ".git" / "rebase-apply").exists()


def test_three_rejections_in_a_row_exit_1(repos):
    """A push that keeps failing for a non-race reason still costs 3 tries."""
    runner = repos["runner"]
    (runner / "data" / "storea.json").write_text('{"seen": ["a","b"]}\n')
    # make pushes fail without making the rebase fail
    git(runner, "remote", "set-url", "--push", "origin", str(runner / "nope.git"))
    r = run_block(runner)
    assert r.returncode == 1
    assert r.stdout.count("Push rejected") == 3
    assert "Could not push the snapshot after 3 attempts." in r.stderr




def test_missing_data_dir_does_not_fail_the_step(repos):
    """`if: always()` means this runs after a failed scrape too; it must not
    turn a diagnosable scrape failure into a confusing git failure."""
    runner = repos["runner"]
    shutil.rmtree(runner / "data")
    git(runner, "rm", "-r", "-q", "--cached", "data")
    git(runner, "commit", "-qm", "drop data")
    r = run_block(runner)
    assert r.returncode == 0, r.stderr


def test_only_data_dir_is_committed(repos):
    runner = repos["runner"]
    (runner / "data" / "storea.json").write_text('{"seen": ["a","b"]}\n')
    (runner / "scratch.txt").write_text("should not be committed\n")
    r = run_block(runner)
    assert r.returncode == 0
    files = git(repos["origin"], "show", "--name-only", "--format=", "main").stdout
    assert "data/storea.json" in files
    assert "scratch.txt" not in files


# --------------------------------------------------- static workflow review --

def test_workflow_static_facts():
    y = WORKFLOW.read_text()
    assert 'cron: "45 */2 * * *"' in y                 # 12 runs/day
    assert "cancel-in-progress: false" in y
    assert "timeout-minutes: 30" in y
    assert 'MAX_PHOTOS: "100"' in y



def test_checkout_fetches_full_history():
    """`git pull --rebase` in the retry block needs more than a depth-1 clone."""
    y = WORKFLOW.read_text()
    assert "actions/checkout@v4" in y
    assert re.search(r"fetch-depth:\s*0", y)


def test_reset_input_expansion_is_empty_on_schedule_runs():
    """`${{ inputs.reset && '--reset' || '' }}` renders '' for schedule events,
    so the command degrades to `python scrape.py ` -- valid."""
    y = WORKFLOW.read_text()
    assert "python scrape.py ${{ inputs.reset && '--reset' || '' }}" in y
    r = subprocess.run(["bash", "-e", "-c", "set -- ; echo \"args=$#\""],
                       capture_output=True, text=True)
    assert r.stdout.strip() == "args=0"


def test_parser_test_step_actually_runs_this_suite():
    """watch.yml runs `python tests/test_parser.py` directly, not pytest."""
    y = WORKFLOW.read_text()
    assert "python tests/test_parser.py" in y
    r = subprocess.run(["python3", "tests/test_parser.py"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
