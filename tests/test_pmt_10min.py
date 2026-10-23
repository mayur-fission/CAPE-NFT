"""Five 10-minute workload scripts side by side, like
test_create_session_through_ui_and_run_script_group_multi_browser.py: every
browser gets one session per script, so PMT_10MIN_SESSIONS sessions (default
10, a multiple of 5) run in PMT_10MIN_SESSIONS / 5 browsers of five tabs
each, all logged in as one user:

    browser 1: PMT_10MIN_MGSOLVE_1, _NLMIXR_1, _PKNCA_1, _PMX_10KB_1, _TIDYVERSE_GGPLOT_1
    browser 2: PMT_10MIN_MGSOLVE_2, ...

each tab running its own script: mrgsolve_sim.R (MGSOLVE_SCRIPT),
nlmixr2_sse_10min.R (NLMIXR_SCRIPT), pknca_nca_10min.R (PKNCA_SCRIPT),
pmx_10kb_10min.R (R_SCRIPT_SMALL_10MIN, the test_concurrent_sessions_10min_s
script) and tidyverse_ggplot2_10min.R (TINYVERSE_GGPLOT_SCRIPT). All five run
for ~10 minutes (test_html_reports_shiny.R is left out: it takes seconds).

First the R packages of all five scripts (the lists in common/r_packages.py,
PMT_10MIN_R_PACKAGES overrides) are checked, and any missing ones installed
into the user's library from a setup session of their own (PMT_10MIN_SETUP),
so the timed runs don't include the install. Once every browser has
launched its sessions, the scripts start in all of them at the same time,
each in its own folder <output dir>/<session name>/, and each run is timed
(timeout PMT_10MIN_SCRIPT_TIMEOUT_MS, default 15 min).

Several machines with the same user (Jenkins USE_EC2_NODES): each sets
PMT_10MIN_NODE_PREFIX (N1_, N2_, ...) so it only touches its own sessions
(N1_PMT_10MIN_MGSOLVE_1, ...), and PMT_10MIN_START_AT_EPOCH so the machines'
logins don't clash.

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time to evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json,
with bar charts of the time per session (common/session_time_report.py) next
to the CSV, all attached to Allure. Set PMT_10MIN_THRESHOLD_S to mark a
slow-session limit on the charts.
"""
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions, script_names
from common.parallel_browsers import wait_until_epoch
from common.r_packages import (
    MGSOLVE_PACKAGES,
    NLMIXR_PACKAGES,
    PKNCA_PACKAGES,
    TIDYVERSE_GGPLOT_PACKAGES,
    install_missing_r_packages,
)
from common.result_printing import summarize_concurrent_runs
from config.config import env, env_list, env_path
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import PMT_10MIN_SESSION_PREFIX

pytestmark = pytest.mark.rstudio_local

# (name in the session names, .env setting with the script's path, its R packages)
WORKLOADS = [
    ("MGSOLVE", "MGSOLVE_SCRIPT", MGSOLVE_PACKAGES),
    ("NLMIXR", "NLMIXR_SCRIPT", NLMIXR_PACKAGES),
    ("PKNCA", "PKNCA_SCRIPT", PKNCA_PACKAGES),
    ("PMX_10KB", "R_SCRIPT_SMALL_10MIN", []),  # base R only
    ("TIDYVERSE_GGPLOT", "TINYVERSE_GGPLOT_SCRIPT", TIDYVERSE_GGPLOT_PACKAGES),
]
USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("PMT_10MIN_SESSIONS", "10"))
# One browser per set of five sessions, a tab per script
BROWSER_COUNT = max(1, SESSION_COUNT // len(WORKLOADS))
# Put in front of every session name when several machines run this test as
# the same user, e.g. "N1_" -> N1_PMT_10MIN_MGSOLVE_1, ...
NODE_PREFIX = env("PMT_10MIN_NODE_PREFIX", "")
NAME_PREFIX = NODE_PREFIX + PMT_10MIN_SESSION_PREFIX
# Browser by browser, one session per script, so that splitting them into
# consecutive chunks of five gives every browser one of each
SESSION_WORKLOADS = [name for _ in range(BROWSER_COUNT) for name, _, _ in WORKLOADS]
SESSION_NAMES = ["%s%s_%d" % (NAME_PREFIX, name, i // len(WORKLOADS) + 1) for i, name in enumerate(SESSION_WORKLOADS)]
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATHS = {name: env(setting) for name, setting, _ in WORKLOADS}
# Unix time to wait for before the first browser starts (blank = start now).
START_AT_EPOCH = env("PMT_10MIN_START_AT_EPOCH") or None
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("PMT_10MIN_BROWSER_STAGGER_S") or env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
# The R packages of all five scripts; the missing ones are installed before
# they run. PMT_10MIN_R_PACKAGES (comma-separated) overrides ("none" = no check).
R_PACKAGES = [p for p in env_list("PMT_10MIN_R_PACKAGES", list(dict.fromkeys(
    p for _, _, packages in WORKLOADS for p in packages))) if p.lower() != "none"]
# The scripts run for ~10 minutes; leave headroom on top.
SCRIPT_TIMEOUT_MS = int(env("PMT_10MIN_SCRIPT_TIMEOUT_MS", "900000"))
# How often each console is checked for the end of its script.
POLL_INTERVAL_MS = int(env("PMT_10MIN_POLL_MS", "500"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "pmt_10min_%d_sessions_%d_browsers_U%d%s_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, "_" + NODE_PREFIX.rstrip("_") if NODE_PREFIX else "",
    time.strftime("%Y%m%d-%H%M%S"))
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("PMT_10MIN_THRESHOLD_S") or 0) or None


def test_pmt_10min_all_workload_scripts_in_every_browser(
    browser_name, browser_type_launch_args, browser_context_args
):
    """Each of the five scripts finishes ok in every browser.

    Flow: check the R packages, installing missing ones (setup session) ->
    per browser: login -> quit leftover sessions with its names -> launch
    its five sessions in tabs -> wait for all browsers -> source each tab's
    script in all tabs at once, each in its own folder -> wait for all ->
    close tabs -> quit created sessions
    """
    unset = [setting for name, setting, _ in WORKLOADS if not SCRIPT_PATHS[name]]
    if unset:
        pytest.skip("%s not set - set them in .env (on Jenkins: in the environment's .env credential) to the "
                    "paths of the scripts" % ", ".join(unset))
    assert SESSION_COUNT >= len(WORKLOADS) and SESSION_COUNT % len(WORKLOADS) == 0, (
        "PMT_10MIN_SESSIONS must be a multiple of %d (one session per script in every browser), got %d"
        % (len(WORKLOADS), SESSION_COUNT))
    wait_until_epoch(START_AT_EPOCH, NODE_PREFIX or "this machine")
    install_missing_r_packages(browser_name, browser_type_launch_args, browser_context_args, USER, R_PACKAGES,
                               NAME_PREFIX + "SETUP")
    scripts = {session: SCRIPT_PATHS[name] for session, name in zip(SESSION_NAMES, SESSION_WORKLOADS)}
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, scripts, OUTPUT_DIR, None, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, poll_interval_ms=POLL_INTERVAL_MS,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per session (five 10-minute workload scripts)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=script_names(scripts),
    )
    assert not errors, "\n".join(errors)
