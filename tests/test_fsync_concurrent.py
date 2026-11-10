"""FSx concurrent test: FSYNC_SESSIONS new RStudio Pro sessions (the
concurrency - how many run at the same time), spread over FSYNC_BROWSERS
browsers (blank = one browser per 5 sessions), all logged in as one user.
Each session gets its own folder, sets up a Python venv there, gets the R and
Python files, and then the R file is sourced in all sessions at once, each
run timed.

Every run gets its own folder on the server,
<FSYNC_BASE>/fsync_<sessions>_sessions_<datetime>/, holding one folder per
session: session1 .. session<FSYNC_SESSIONS> (FSYNC_SESSION_<N> works in
session<N>). Nothing is deleted afterwards.

Per session (common/fsync_setup.py), before the scripts start:
  setwd to its folder ->
  cd $BASE && pwd ; which python3 && python3 --version ; df -h $BASE ->
  python3 -m venv <session folder>/venv ; <session folder>/venv/bin/python --version ->
  R + Python files into the folder (FSYNC_FILES_MODE: "copy" from the server
  paths FSYNC_R_FILE / FSYNC_PY_FILE, or "upload" the local files through
  the Files pane's Upload button) -> setwd via Files pane > More > Set As
  Working Directory -> Sys.setenv(FSYNC_SESSION_DIR, FSYNC_VENV_PYTHON,
  FSYNC_PY_FILE) for the R script
Then source(<session folder>/<R file>) in all sessions at once; each session waits until
its R file ends by itself (no time limit - the R file sets its own run time).

Results: evidence/fsync_setup_<TIMINGS_LABEL>.csv (setup per session: status,
setup and copy/upload time, python versions, df -h), and as in test_168
evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv,
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json and the session time
charts. All are attached to Allure.
"""
import os
import posixpath
import threading
import time

import pytest

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.fsync_setup import setup_session, write_setup_csv
from common.rstudio_console_commands import get_console_output
from common.parallel_browsers import browsers_for
from common.perf_report import attach_allure_file
from common.result_printing import summarize_concurrent_runs
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_naming_logic import FSYNC_SESSION_PREFIX, concurrent_session_names
from common.session_time_report import describe_run, generate_session_time_report
from common.evidence import save_screenshot
from config.config import env, env_list, evidence_dir

pytestmark = pytest.mark.rstudio_local

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER = int(env("FSYNC_USER", "1"))
# The concurrency: sessions created, set up and running the R file at the same time
SESSION_COUNT = int(env("FSYNC_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("FSYNC_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=FSYNC_SESSION_PREFIX)
SESSION_FOLDERS = {name: "session%d" % (i + 1) for i, name in enumerate(SESSION_NAMES)}
# $BASE of the setup commands; the run folder goes in it
BASE = (env("FSYNC_BASE") or "/fsx/fsx_data").rstrip("/") or "/"
# "copy": FSYNC_R_FILE / FSYNC_PY_FILE are server paths; "upload": local paths
# (relative ones resolve against the repo folder)
FILES_MODE = (env("FSYNC_FILES_MODE") or "copy").strip().lower()
R_FILE = env("FSYNC_R_FILE")
PY_FILE = env("FSYNC_PY_FILE")
PYTHON = env("FSYNC_PYTHON") or "python3"
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
RUN_FOLDER = "fsync_%d_sessions_%s" % (SESSION_COUNT, RUN_STARTED)
RUN_DIR = posixpath.join(BASE, RUN_FOLDER)
BROWSER_STAGGER_S = int(env("FSYNC_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("FSYNC_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MIN = float(env("FSYNC_LAUNCH_TIMEOUT_MIN", "3"))
LAUNCH_RETRY_WAIT_S = int(env("FSYNC_LAUNCH_RETRY_WAIT_S", "15"))
# Longest one setup command (e.g. creating the venv) may take, in minutes
SETUP_TIMEOUT_MIN = float(env("FSYNC_SETUP_TIMEOUT_MIN") or 10)
# The test never stops the R file: it runs as long as the file itself says
# (run_seconds etc. in run_load.R) and each session waits until it ends by
# itself. The shared runner needs a number, so it gets one far beyond any
# real run (7 days, in minutes) - it is not a test duration.
NO_LIMIT_MIN = 7 * 24 * 60
# Console lines printed per session once its R file has finished
CONSOLE_TAIL_LINES = int(env("FSYNC_CONSOLE_TAIL_LINES", "25"))
# Text the R file prints when its load really starts/ends; a session whose
# console lacks any of it fails (comma-separated; "none" = no check)
EXPECTED_OUTPUT = [t for t in env_list("FSYNC_EXPECTED_OUTPUT", ["RUN START"]) if t.lower() != "none"]
# A console with this many lines may have dropped its first ones (RStudio
# keeps the last ~1000), so a missing text there is only a warning
CONSOLE_FULL_LINES = int(env("FSYNC_CONSOLE_FULL_LINES", "900"))
POLL_INTERVAL_MS = int(env("FSYNC_POLL_MS", "500"))
THRESHOLD_S = float(env("FSYNC_THRESHOLD_S") or 0) or None
TIMINGS_LABEL = RUN_FOLDER


def _file_paths():
    """[R file, Python file (if set)] as given for FILES_MODE: local files
    made absolute (and checked) for "upload", server paths as they are."""
    paths = [p for p in (R_FILE, PY_FILE) if p]
    if FILES_MODE == "upload":
        paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in paths]
        missing = [p for p in paths if not os.path.isfile(p)]
        assert not missing, "FSYNC_FILES_MODE=upload: local files not found: %s" % missing
    return paths


def _check_run_output(page, record):
    """Once a session's R file has finished: print the end of its console
    (the R file's own output) in the test log, and fail the run if the
    console lacks any of EXPECTED_OUTPUT (e.g. "RUN START": the R file
    ended before starting its load)."""
    name = record["session_name"]
    try:
        console = get_console_output(page)
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not read its console: %s" % (name, exc))
        if record.get("status") == "ok":
            record["status"] = "could not check its output: %s" % " ".join(str(exc).split())[:150]
        return
    tail = console.strip().splitlines()[-CONSOLE_TAIL_LINES:]
    print("\n[rstudio-local] session %s: R file finished (%s); console ends with:\n%s"
          % (name, record.get("status"), "\n".join(tail)))
    missing = [text for text in EXPECTED_OUTPUT if text not in console]
    lines = console.count("\n") + 1
    if missing and lines >= CONSOLE_FULL_LINES:
        # The console keeps only its last lines: on a long output the text
        # may have scrolled away, which says nothing about the run
        print("\n[rstudio-local] WARNING: session %s: %s not in its console, but the console is full "
              "(%d lines) and may have dropped it - not counted as a failure" % (name, missing, lines))
    elif missing and record.get("status") == "ok":
        record["status"] = "R file ended without printing %s" % ", ".join(repr(t) for t in missing)
        save_screenshot(page, "fsync_missing_output_%s.png" % name, always=True)


def test_fsync_concurrent_sessions(browser_name, browser_type_launch_args, browser_context_args):
    """FSYNC_SESSIONS sessions each set up their own folder (venv + R/Python
    files) and then run the R file at the same time; every setup and run is
    reported.

    Flow per browser: login -> quit leftover sessions with its names ->
    launch each in a tab -> create each session's folder, setwd there ->
    setup (shell checks, venv, copy/upload files, setwd) in each -> wait for
    all browsers -> source the R file in all tabs at once -> wait until all
    finish -> close tabs -> quit the sessions. Then: setup CSV, timings CSV
    and charts.
    """
    if not R_FILE:
        pytest.skip("FSYNC_R_FILE is not set - set it in .env to the R file to run (a server path with "
                    "FSYNC_FILES_MODE=copy, a local one with FSYNC_FILES_MODE=upload)")
    assert FILES_MODE in ("copy", "upload"), "FSYNC_FILES_MODE must be copy or upload, got %r" % FILES_MODE
    assert SESSION_COUNT >= 1, "FSYNC_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    files = _file_paths()
    print("\n[rstudio-local] fsync run folder: %s (%d sessions, %d browsers, files %s: %s)"
          % (RUN_DIR, SESSION_COUNT, BROWSER_COUNT, FILES_MODE, ", ".join(files)))

    setup_records = {}
    lock = threading.Lock()

    def _setup(launched, prepared):
        for session in launched:
            session_dir, error = prepared.get(session.session_name, (None, None))
            if session.error or error is not None or not session_dir:
                continue
            record = setup_session(session.page, session.session_name, session_dir, BASE, files,
                                   mode=FILES_MODE, python=PYTHON, timeout_ms=int(SETUP_TIMEOUT_MIN * 60000))
            with lock:
                setup_records[session.session_name] = record
            if "error" in record:
                prepared[session.session_name] = (session_dir, record["error"])

    scripts = {name: posixpath.join(RUN_DIR, folder, posixpath.basename(R_FILE))
               for name, folder in SESSION_FOLDERS.items()}
    sessions_per_browser = -(-SESSION_COUNT // BROWSER_COUNT)
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, scripts, RUN_DIR, None, int(NO_LIMIT_MIN * 60000),
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS,
        launch_timeout_ms=int(LAUNCH_TIMEOUT_MIN * 60000), retry_wait_s=LAUNCH_RETRY_WAIT_S,
        delete_output=False, poll_interval_ms=POLL_INTERVAL_MS, session_folders=SESSION_FOLDERS,
        after_run=_check_run_output, after_prepare=_setup, after_prepare_s=sessions_per_browser * (SETUP_TIMEOUT_MIN * 60 + 300),
    )

    setup_csv = os.path.join(evidence_dir(), "fsync_setup_%s.csv" % TIMINGS_LABEL)
    write_setup_csv(setup_csv, [setup_records[n] for n in SESSION_NAMES if n in setup_records])
    attach_allure_file(setup_csv)
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs, scripts=scripts)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session - fsync (%s)" % posixpath.basename(R_FILE),
        threshold=THRESHOLD_S, browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=R_FILE,
    )
    assert not errors, "\n".join(errors)
