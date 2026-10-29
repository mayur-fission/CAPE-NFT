"""Like test_concurrent_sessions_10min_s.py, but sources the mrgsolve
simulation script mrgsolve_sim.R (MRGSOLVE_SCRIPT). MRGSOLVE_SESSIONS
sessions (default 1) launched from the UI, named MRGSOLVE_SESSION_1 ..
_<N>, spread over MRGSOLVE_SESSIONS_BROWSERS browsers (blank = one
browser per 5 sessions), all logged in as one user. Once every browser has
launched its sessions, SCRIPT_PATH is sourced in all of them at the same time
and each run is timed (timeout MRGSOLVE_SCRIPT_TIMEOUT_MS, default 15 min).

First the R packages the script needs (MRGSOLVE_R_PACKAGES) are checked, and
any missing ones installed into the user's library, from a setup session of
their own (MRGSOLVE_SESSION_SETUP), so the timed runs don't include the install.

Every run gets its own folder, <output dir>/MRGSOLVE_USER_<session count>_<datetime>/
(e.g. MRGSOLVE_USER_5_20261029-203754), and in it each session runs the script in its own
folder: MRGSOLVE_SESSION_<N> in MRGSOLVE_OUTPUT_SESSION_<N>/.
Set MRGSOLVE_OUTPUT_FILES (comma-separated, relative to the session
folder) to the files the script writes, and a run only counts as ok if all of
them are there afterwards (never deleted: the data stays on the server after the
test); unset, only the script finishing without
an error is checked. MRGSOLVE_RUN_SUMMARY names a run summary file in the
pmx scripts' format (replicates, runtime, stop reason) to read into the
results (unset = none).

Per-session timings go to evidence/rstudio_script_timings_<TIMINGS_LABEL>.csv
and the overall time (first start to last finish, min/avg/max) to
evidence/rstudio_concurrent_summary_<TIMINGS_LABEL>.json, with bar charts of
the time per session (common/session_time_report.py) next to the CSV, all
attached to Allure along with the screenshots. Set
MRGSOLVE_THRESHOLD_S to mark a slow-session limit on the charts.

Each finished session's mrgsolve_run_summary_PID_<run id>.csv (written by
the script in its session folder) is read into a run summary table, one row
per session: evidence/mrgsolve_run_summary_<TIMINGS_LABEL>.csv, printed and
attached to Allure as a table.
"""
import csv
import html
import io
import os
import posixpath
import time

import pytest

try:
    import allure
except ImportError:
    allure = None

from common.concurrent_sessions import run_script_in_concurrent_sessions
from common.result_printing import summarize_concurrent_runs
from common.parallel_browsers import browsers_for
from common.r_packages import MGSOLVE_PACKAGES as MRGSOLVE_PACKAGES, install_missing_r_packages
from common.perf_report import attach_allure_file, write_csv_report
from common.rstudio_console_commands import read_console_value, read_text_file
from common.step_log import log_step
from config.config import env, env_list, env_path, evidence_dir
from common.script_timings import script_timings_csv_path, write_script_timings_csv
from common.session_time_report import describe_run, generate_session_time_report
from common.session_naming_logic import MRGSOLVE_SESSION_PREFIX, concurrent_session_names

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
SESSION_COUNT = int(env("MRGSOLVE_SESSIONS", "1"))
BROWSER_COUNT = browsers_for(SESSION_COUNT, int(env("MRGSOLVE_SESSIONS_BROWSERS") or 0))
SESSION_NAMES = concurrent_session_names(SESSION_COUNT, prefix=MRGSOLVE_SESSION_PREFIX)
# MRGSOLVE_SESSION_<N> writes its output to MRGSOLVE_OUTPUT_SESSION_<N>
SESSION_FOLDERS = {name: "MRGSOLVE_OUTPUT_SESSION_%d" % (i + 1) for i, name in enumerate(SESSION_NAMES)}
# Gap between starting one browser and the next, so their logins don't clash.
BROWSER_STAGGER_S = int(env("CONCURRENT_SESSIONS_BROWSER_STAGGER_S", "30"))
LAUNCH_ATTEMPTS = int(env("CONCURRENT_SESSIONS_LAUNCH_ATTEMPTS", "3"))
LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))
LAUNCH_RETRY_WAIT_S = int(env("CONCURRENT_SESSIONS_LAUNCH_RETRY_WAIT_S", "15"))
# Checked in the test, not here: a missing setting skips only this test
# instead of failing the collection of every other one.
SCRIPT_PATH = env("MRGSOLVE_SCRIPT")
# R packages the script needs; the missing ones are installed before it runs.
# MRGSOLVE_R_PACKAGES (comma-separated) overrides ("none" = no check).
R_PACKAGES = [p for p in env_list("MRGSOLVE_R_PACKAGES", MRGSOLVE_PACKAGES) if p.lower() != "none"]
OUTPUT_DIR = env_path("OUTPUT_DIR_CONCURRENT_SESSIONS", "CONCURRENT_SESSIONS_OUTPUT_DIR",
                      "OUTPUT_DIR_GENERATE_CSV", "CSV_SESSIONS_OUTPUT_DIR")
RUN_STARTED = time.strftime("%Y%m%d-%H%M%S")
# This run's own folder, holding the session folders
RUN_DIR = posixpath.join(OUTPUT_DIR, "MRGSOLVE_USER_%d_%s" % (SESSION_COUNT, RUN_STARTED)) if OUTPUT_DIR else None
# Files the script writes under its session folder; all must exist for a run
# to count as ok. Unset = no output check.
OUTPUT_FILES = env_list("MRGSOLVE_OUTPUT_FILES")
RUN_SUMMARY_FILE = env("MRGSOLVE_RUN_SUMMARY", "")
# The script runs for ~10 minutes; leave headroom on top.
SCRIPT_TIMEOUT_MS = int(env("MRGSOLVE_SCRIPT_TIMEOUT_MS", "900000"))
# How often each console is checked for the end of the script.
POLL_INTERVAL_MS = int(env("MRGSOLVE_POLL_MS", "500"))
# Run start time in the name: every run writes its own CSV, charts and
# summary instead of overwriting another's
TIMINGS_LABEL = "mrgsolve_%d_sessions_%d_browsers_U%d_%s" % (
    SESSION_COUNT, BROWSER_COUNT, USER, RUN_STARTED)
# Slow-session limit drawn on the session time charts (blank = none).
THRESHOLD_S = float(env("MRGSOLVE_THRESHOLD_S") or 0) or None
# The run summary the script writes in its session folder (newest one if several)
RUN_SUMMARY_PATTERN = r"^mrgsolve_run_summary_PID_.*\\.csv$"
# Run summary table: (column in the script's CSV, report header, decimals or None)
# - the first column found of each tuple of names is used
SUMMARY_COLUMNS = [
    (("run_id",), "Run Id", None),
    (("iterations", "iteration"), "Iterations", None),
    (("sims_per_iteration",), "Sims Per Iterations", None),
    (("total_simulations",), "Total Simulations", None),
    (("mean_iteration_sec",), "Mean Iteration Time", 2),
    (("mean_sim_sec", "mean_simulation_sec"), "Mean Simulation Time", 2),
    (("min_iteration_sec",), "Min", 2),
    (("max_iteration_sec",), "Max", 2),
    (("simulations_per_min",), "Simulations/minute", 2),
    (("subject_sims_per_sec",), "Subject sims/second", 2),
]


def read_mrgsolve_summary(page, record):
    """after_run hook: read the session's mrgsolve_run_summary_PID_*.csv
    (from its working directory) into record["mrgsolve_summary"]. A summary
    that cannot be read is only logged - it does not fail the run."""
    name = record["session_name"]
    try:
        path = read_console_value(
            page, "{f <- list.files(pattern='%s'); if (length(f)) f[which.max(file.mtime(f))] else ''}"
            % RUN_SUMMARY_PATTERN, timeout_ms=30000).strip()
        if not path:
            log_step("WARNING: no mrgsolve_run_summary_PID_*.csv in the folder of session %s", name)
            return
        text = read_text_file(page, path)
        rows = list(csv.DictReader(io.StringIO(text or "")))
        if not rows:
            log_step("WARNING: run summary %s in session %s is empty", path, name)
            return
        record["mrgsolve_summary"] = rows[-1]
        log_step("Run summary %s read in session %s", path, name)
    except Exception as exc:
        log_step("WARNING: could not read the run summary in session %s: %s", name,
                 " ".join(str(exc).split())[:200])


def _summary_cell(summary, names, decimals):
    value = next((summary[n] for n in names if summary.get(n) not in (None, "")), "")
    if decimals is None or value in ("", "NA"):
        return value
    try:
        return "%.*f" % (decimals, float(value))
    except ValueError:
        return value


def save_summary_table_png(path, head, rows, title):
    """The run summary table drawn as an image at `path`, in the style of the
    session time charts' stats table. Returns the path, or None (logged) if
    it could not be drawn."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        log_step("WARNING: run summary image not drawn (matplotlib missing): %s", exc)
        return None
    # two-line headers keep the columns narrow
    labels = [h.replace(" ", "\n", 1) if len(h) > 12 else h for h in head]
    fig, ax = plt.subplots(figsize=(max(10, 1.25 * len(head)), 0.8 + 0.2 * title.count("\n") + 0.3 * (len(rows) + 1)))
    ax.axis("off")
    ax.set_title(title, fontsize=11, fontweight="bold", loc="left")
    table = ax.table(cellText=rows or [[""] * len(head)], colLabels=labels, loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.6)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if r == 0:
            cell.set_facecolor("#e8ecf2")
            cell.set_text_props(fontweight="bold")
            cell.set_height(cell.get_height() * 1.6)
        elif c == 0:
            cell.set_text_props(fontweight="bold", ha="left")
        elif r % 2 == 0:
            cell.set_facecolor("#f7f7f7")
    table.auto_set_column_width(list(range(len(head))))
    fig.text(0.01, 0.01, "Generated: %s" % time.strftime("%Y-%m-%d %H:%M:%S"), fontsize=8, color="#666")
    try:
        fig.savefig(path, dpi=150, bbox_inches="tight")
    except Exception as exc:
        log_step("WARNING: could not save the run summary image %s: %s", path, exc)
        return None
    finally:
        plt.close(fig)
    print("[rstudio-local] wrote the run summary image to %s" % path)
    return path


def report_run_summaries(runs, label):
    """The run summary table (SUMMARY_COLUMNS, one row per session, in
    session order): written to evidence/mrgsolve_run_summary_<label>.csv,
    printed and attached to Allure as an HTML table. Sessions with no
    summary get a row of blanks."""
    head = ["Session Name"] + [header for _, header, _ in SUMMARY_COLUMNS]
    rows = []
    for run in sorted(runs, key=lambda r: int(r["session_name"].rsplit("_", 1)[-1])):
        summary = run.get("mrgsolve_summary") or {}
        rows.append([run["session_name"]] + [_summary_cell(summary, names, decimals) if summary else ""
                                             for names, _, decimals in SUMMARY_COLUMNS])
    path = os.path.join(evidence_dir(), "mrgsolve_run_summary_%s.csv" % label)
    write_csv_report(path, [head] + rows)
    widths = [max(len(str(row[i])) for row in [head] + rows) for i in range(len(head))]
    print("\n[rstudio-local] mrgsolve run summary (%s):" % path)
    for row in [head] + rows:
        print("  " + " | ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))
    png = save_summary_table_png(os.path.splitext(path)[0] + ".png", head, rows,
                                 "mrgsolve run summary - %s\nParent folder: %s"
                                 % (describe_run(SESSION_COUNT, BROWSER_COUNT, USER), RUN_DIR))
    if png:
        attach_allure_file(png, name="mrgsolve run summary (image)")
    if allure is not None:
        table = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(str(c)) for c in row) for row in rows)
        allure.attach(
            "<html><body><table border='1' cellpadding='4' style='border-collapse:collapse;font-family:sans-serif'>"
            "<tr>%s</tr>%s</table></body></html>" % ("".join("<th>%s</th>" % html.escape(h) for h in head), table),
            name="mrgsolve run summary", attachment_type=allure.attachment_type.HTML)


def test_mrgsolve_r_script(browser_name, browser_type_launch_args, browser_context_args):
    """The mrgsolve R script finishes ok (with its output files, when
    MRGSOLVE_OUTPUT_FILES is set) in every session, each session's
    output in its MRGSOLVE_OUTPUT_SESSION_<N> folder of this run's folder.

    Flow: check the R packages, installing missing ones (setup session) ->
    per browser: login -> quit leftover sessions with its names ->
    launch each name in a tab -> wait for all browsers -> source the script
    in all tabs at once, each in its own folder -> wait for all -> check each
    session's output files (reading its run summary) -> close tabs -> quit
    created sessions -> run summary table
    """
    if not SCRIPT_PATH:
        pytest.skip("MRGSOLVE_SCRIPT is not set - set it in .env (on Jenkins: in the environment's .env "
                    "credential) to the path of mrgsolve_sim.R")
    assert SESSION_COUNT >= 1, "MRGSOLVE_SESSIONS must be at least 1, got %d" % SESSION_COUNT
    install_missing_r_packages(browser_name, browser_type_launch_args, browser_context_args, USER, R_PACKAGES,
                               MRGSOLVE_SESSION_PREFIX + "SETUP")
    runs, errors = run_script_in_concurrent_sessions(
        browser_name, browser_type_launch_args, browser_context_args, USER, SESSION_NAMES, BROWSER_COUNT,
        TIMINGS_LABEL, SCRIPT_PATH, RUN_DIR, OUTPUT_FILES, SCRIPT_TIMEOUT_MS,
        stagger_s=BROWSER_STAGGER_S, attempts=LAUNCH_ATTEMPTS, launch_timeout_ms=LAUNCH_TIMEOUT_MS,
        retry_wait_s=LAUNCH_RETRY_WAIT_S, delete_output=False, poll_interval_ms=POLL_INTERVAL_MS,
        summary_file=RUN_SUMMARY_FILE or None, session_folders=SESSION_FOLDERS,
        after_run=read_mrgsolve_summary,
    )
    timings_csv = script_timings_csv_path(TIMINGS_LABEL)
    write_script_timings_csv(timings_csv, runs)
    summarize_concurrent_runs(runs, TIMINGS_LABEL, SESSION_COUNT, BROWSER_COUNT)
    report_run_summaries(runs, TIMINGS_LABEL)
    generate_session_time_report(
        timings_csv, title="Time taken per concurrent session (mrgsolve script)", threshold=THRESHOLD_S,
        browser=browser_name, test_name=describe_run(SESSION_COUNT, BROWSER_COUNT, USER),
        script=SCRIPT_PATH,
    )
    assert not errors, "\n".join(errors)
