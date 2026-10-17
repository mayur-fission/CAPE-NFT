"""Settings from .env (loaded on import, so pytest works in any shell),
option parsing, and where this run's evidence goes.
"""
import os
import re
import sys
import time

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Shared by every run: allure-results, JUnit files, the cross-process login
# lock (common/session_retry.py), Jenkins' stop/ready files.
EVIDENCE_ROOT = os.path.join(REPO_ROOT, "evidence")


# Start time of this pytest run, shared with pytest-xdist workers (they are
# started by this process and inherit it), so test files run in parallel
# get folders with the same time stamp.
RUN_STAMP = os.environ.setdefault("EVIDENCE_RUN_STAMP", time.strftime("%Y%m%d-%H%M%S"))


def current_test_file():
    """Name (no .py) of the test file running now, from pytest's
    PYTEST_CURRENT_TEST (seen by every thread of the process), else None."""
    current = os.environ.get("PYTEST_CURRENT_TEST", "")
    return os.path.splitext(os.path.basename(current.split("::")[0]))[0] or None


def evidence_dir(test_file=None):
    """The folder evidence goes to, created on first use: under pytest one
    folder per test file per run, evidence/runs/<run start>_<test file>/ -
    so test files run in parallel (pytest -n) each get their own; outside
    pytest (helper scripts, the chart CLI) evidence/ itself."""
    test_file = test_file or current_test_file()
    if "pytest" not in sys.modules or not test_file:
        path = EVIDENCE_ROOT
    else:
        path = os.path.join(EVIDENCE_ROOT, "runs", "%s_%s" % (RUN_STAMP, re.sub(r"[^\w.-]", "_", test_file)[:120]))
    os.makedirs(path, exist_ok=True)
    return path

# override=True so a stale value left in the shell (e.g. an old API token)
# can't shadow .env.
load_dotenv(os.path.join(REPO_ROOT, ".env"), override=True)


def env(name, default=None, required=False):
    """Environment variable `name`, else `default`. With required=True,
    raises RuntimeError when it is unset or empty."""
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(
            "environment variable %s is not set. Set it in .env and source it." % name
        )
    return v


def env_path(*names):
    """The first of `names` that is set (non-empty) in the environment/.env.
    Server paths (folders, R scripts, source() commands) have no defaults in
    the code: they come from .env only, so raises RuntimeError naming the
    settings when none is set."""
    for name in names:
        v = os.environ.get(name)
        if v:
            return v
    raise RuntimeError(
        "%s is not set. Server paths have no defaults - set it in .env (see README, Server paths)."
        % " or ".join(names)
    )


def env_list(name, default=None):
    """Comma-separated env var as a list of non-empty, stripped items, or
    `default` when it is unset or empty."""
    v = os.environ.get(name)
    items = [item.strip() for item in v.split(",") if item.strip()] if v else []
    return items or default


def browser_window_args():
    """Chromium arguments for headed windows: maximized, plus a fixed
    --window-size when BROWSER_WINDOW_SIZE is set (e.g. "1920,1080"). Jenkins
    sets it for headed runs on a virtual display (Xvfb), which has no window
    manager, so --start-maximized alone does nothing there.

    Also --disable-dev-shm-usage: Docker gives /dev/shm only 64MB, and
    Chromium tabs crash ("Target crashed") once it fills up, e.g. with
    many RStudio tabs open for a long script run."""
    args = ["--start-maximized", "--disable-dev-shm-usage"]
    size = env("BROWSER_WINDOW_SIZE")
    if size:
        args.append("--window-size=%s" % size)
    return args


def default_session_count():
    """RSTUDIO_SESSION_COUNT (default 10): sessions per multi-session run."""
    return int(env("RSTUDIO_SESSION_COUNT", "10"))


def max_workers_for(count, max_workers=None, env_name="RSTUDIO_CONCURRENT_MAX_WORKERS"):
    """Parallel browsers for a concurrent run of `count` sessions: max_workers,
    else env var `env_name`, else one per session (at least 1)."""
    from_env = env(env_name)
    return max(1, max_workers or (int(from_env) if from_env else None) or count)


# --- Option parsing ------------------------------------------------------------


def optional_int(value):
    """int(value), or None when the env value is unset/empty."""
    return int(value) if value else None


def optional_float(value):
    """float(value), or None when the env value is unset/empty."""
    return float(value) if value else None


def is_headless(request):
    """False only when pytest ran with --headed (for browsers the tests open
    themselves, outside the pytest-playwright fixtures)."""
    return not bool(request.config.getoption("--headed", default=False))
