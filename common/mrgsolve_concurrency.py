"""Server-side start, pacing and timings for test_mrgsolve_concurrent_new
(only that test uses this module).

Each session sources a small wrapper R script (written into its SESSION_<N>
folder just before the scripts are submitted) instead of the mrgsolve
script itself. The wrapper:
  1. notes the R process uptime and id (how long the session had been up
     without running anything, and whether R was restarted - a suspended
     session that was resumed);
  2. spike mode (a start time): waits on the server clock until it, so the
     sessions of every workspace start within ~0.2s of each other; as-ready
     mode (no start time): starts at once;
  3. sources the mrgsolve script `iterations` times, pausing `think_time_s`
     between runs (Sys.sleep: R stays busy, so the session is never idle
     long enough to be suspended), one timing row per run - server start/end
     time, host node, R process id;
  4. after every run rewrites timing.csv in its session folder and
     <group dir>/<session name>.csv - the group dir
     (MRGSOLVE_CONCURRENCY_<run tag>) is shared by every workspace of the
     run, one file per session, so the concurrency report can read them all.

concurrency_stats() then shows how far the runs overlapped: peak number
running at once, the window in which every session's first run was
running, the spread of the start times.
"""
import csv
import io
import os
import posixpath
import re
import time
from datetime import datetime

from common.mrgsolve_summary import read_mrgsolve_summary
from common.perf_report import attach_allure_file, write_csv_report, write_json_report
from common.rstudio_console_commands import (
    r_ignored_error_condition, r_string, read_console_value, read_text_file, read_text_lines,
)
from common.step_log import log_step
from config.config import evidence_dir

WRAPPER_FILE_NAME = "mrgsolve_wrapper.R"
TIMING_FILE_NAME = "timing.csv"
GROUP_DIR_PREFIX = "MRGSOLVE_CONCURRENCY_"
# How often a waiting wrapper looks at the clock: the most the starts can differ by
SERVER_WAIT_STEP_S = 0.2
# A session launched before another in the same browser has been up longer;
# up this much less than the session launched after it = R was restarted
RESTART_SLACK_S = 30


# --- Start time and run tag ---------------------------------------------------------
def parse_start_at(value):
    """MRGSOLVE_START_AT as a Unix epoch (float), or None if blank.

    Accepts a Unix epoch ("1763560800"), "HH:MM" / "HH:MM:SS" (today, this
    machine's local time) or "YYYY-MM-DD HH:MM[:SS]" (local time). Use the
    epoch form when the workspaces' machines are in different time zones.
    """
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        if not fmt.startswith("%Y"):
            parsed = datetime.combine(datetime.now().date(), parsed.time())
        return time.mktime(parsed.timetuple())
    raise ValueError("MRGSOLVE_START_AT %r is not a Unix epoch, HH:MM[:SS] or YYYY-MM-DD HH:MM[:SS]" % value)


def run_tag(tag, start_at):
    """The name shared by every workspace of one run: MRGSOLVE_RUN_TAG if
    set (letters, digits, - and _ only), else the start epoch; None if
    neither (no shared folder, no cross-workspace report)."""
    tag = (tag or "").strip()
    if tag:
        if not re.match(r"^[A-Za-z0-9_-]+$", tag):
            raise ValueError("MRGSOLVE_RUN_TAG %r may only hold letters, digits, - and _" % tag)
        return tag
    return "%d" % int(start_at) if start_at else None


def group_dir(working_dir, tag):
    """<working_dir>/MRGSOLVE_CONCURRENCY_<tag> - shared by every workspace
    of the run (None without a working dir or tag)."""
    if not working_dir or not tag:
        return None
    return posixpath.join(working_dir, GROUP_DIR_PREFIX + tag)


def format_epoch(epoch_s):
    """Local "YYYY-MM-DD HH:MM:SS.mmm", "" for None."""
    if epoch_s is None:
        return ""
    return datetime.fromtimestamp(epoch_s).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def start_interval_s(ramp_up_s, interval_s, total_sessions):
    """Seconds between two sessions' starts: MRGSOLVE_RAMP_UP_S spread over
    every session of the run (e.g. 600s / 200 sessions = 3s), else
    MRGSOLVE_START_INTERVAL_S, else 0 (all at once)."""
    if ramp_up_s and total_sessions > 1:
        return float(ramp_up_s) / total_sessions
    return float(interval_s or 0)


def start_schedule(session_names, start_at, interval_s, workspace_index=1, workspace_count=1):
    """{session name: its server start time}. Sessions are interleaved over
    the workspaces: slot = (N - 1) * workspace_count + (workspace_index - 1),
    so with 5 workspaces U1-S1 starts at +0s, U2-S1 at +1 interval, ...,
    U5-S1 at +4, U1-S2 at +5 ... - one start every `interval_s`, run-wide.
    Without `start_at` (as-ready mode) there is no schedule: {}."""
    if not start_at:
        return {}
    return {name: start_at + (i * workspace_count + workspace_index - 1) * interval_s
            for i, name in enumerate(session_names)}


def console_timeout_s(start_at, script_timeout_s, iterations=1, think_time_s=0.0, now=None):
    """How long a session's console may take from its submit: the wait for
    the start time (spike mode), every run at its timeout, and the think
    time between runs."""
    wait_s = max(0.0, start_at - (now if now is not None else time.time())) if start_at else 0.0
    return wait_s + iterations * script_timeout_s + max(0, iterations - 1) * think_time_s


# --- Wrapper --------------------------------------------------------------------------
def wrapper_source(script_path, session_name, suffix, session_dir, shared_dir, start_at,
                   iterations=1, think_time_s=0.0):
    """R code of a session's wrapper (see the module docstring). Without
    `start_at` it starts at once; without `shared_dir` it writes only its
    own timing.csv."""
    iterations = max(1, int(iterations))
    save = ["    write.csv(.rows, file.path(%s, %s), row.names = FALSE)"
            % (r_string(session_dir), r_string(TIMING_FILE_NAME))]
    if shared_dir:
        save.append("    write.csv(.rows, file.path(%s, %s), row.names = FALSE)"
                    % (r_string(shared_dir), r_string(session_name + ".csv")))
    lines = [
        "local({",
        "  .uptime <- as.numeric(proc.time()[['elapsed']])",
        "  .rows <- NULL",
    ]
    if shared_dir:
        lines.append("  dir.create(%s, showWarnings = FALSE, recursive = TRUE)" % r_string(shared_dir))
    if start_at:
        lines.append("  while (as.numeric(Sys.time()) < %.3f) Sys.sleep(%s)" % (start_at, SERVER_WAIT_STEP_S))
    lines += [
        # The client script may setwd(); each run starts in, and returns to, the
        # session folder, where timing.csv, the run summary and the outputs are read.
        "  .wd <- getwd()",
        "  .scheduled <- %s" % ("sprintf('%%.3f', %.3f)" % start_at if start_at else "''"),
        "  .status <- 'ok'",
        "  for (.it in seq_len(%d)) {" % iterations,
    ]
    if think_time_s and iterations > 1:
        lines.append("    if (.it > 1) Sys.sleep(%s)" % float(think_time_s))
    script_name = posixpath.basename(script_path)
    lines += [
        "    .s <- as.numeric(Sys.time())",
        "    message(sprintf('[mrgsolve] %%s run %%d started at %%s in session %%s', %s, .it,"
        " format(Sys.time(), '%%Y-%%m-%%d %%H:%%M:%%OS3'), %s))" % (r_string(script_name), r_string(session_name)),
        "    .status <- tryCatch({ source(%s, local = globalenv()); 'ok' }," % r_string(script_path),
        "                        error = function(e) {",
        "                          .m <- gsub('[\\r\\n]+', ' ', conditionMessage(e))",
        # Errors from the client's original script (IGNORED_R_ERROR_PATTERNS) do not fail the run.
        "                          if (%s) { message('[mrgsolve] ignoring R error from the client script: ', .m); 'ok' }"
        % r_ignored_error_condition(".m"),
        # Capped so a timing row stays under RStudio's console line length limit when read back.
        "                          else paste('R error:', substr(.m, 1, 300))",
        "                        })",
        "    .e <- as.numeric(Sys.time())",
        "    try(setwd(.wd), silent = TRUE)",
        "    message(sprintf('[mrgsolve] %%s run %%d ended at %%s in session %%s after %%.2fs (%%s)', %s, .it,"
        " format(Sys.time(), '%%Y-%%m-%%d %%H:%%M:%%OS3'), %s, .e - .s, .status))"
        % (r_string(script_name), r_string(session_name)),
        "    .rows <- rbind(.rows, data.frame(session = %s, suffix = %s, script = %s, iteration = .it,"
        % (r_string(session_name), r_string(suffix or ""), r_string(script_name)),
        "      host = Sys.info()[['nodename']], pid = Sys.getpid(), r_uptime_at_submit = sprintf('%.1f', .uptime),",
        "      scheduled_start = .scheduled, start = sprintf('%.3f', .s), end = sprintf('%.3f', .e), status = .status,",
        "      stringsAsFactors = FALSE))",
    ] + save + [
        "    if (.status != 'ok') break",
        "  }",
        "  if (.status != 'ok') stop(.status)",
        "})",
    ]
    return "\n".join(lines) + "\n"


def wrapper_path(run_dir, folder):
    """<run_dir>/<folder>/mrgsolve_wrapper.R"""
    return posixpath.join(run_dir, folder, WRAPPER_FILE_NAME)


def flag_restarts(uptimes, slack_s=RESTART_SLACK_S):
    """Names whose R was restarted, from [(name, R uptime)] in launch order
    (one browser): a session launched earlier has been up longer, so one up
    more than `slack_s` less than the session launched after it was
    restarted - suspended, then resumed."""
    return {name for (name, up), (_, up_next) in zip(uptimes, uptimes[1:]) if up < up_next - slack_s}


class WrapperWriter:
    """The after_prepare hook for run_script_in_concurrent_sessions(): in
    each prepared session, write its wrapper into its session folder through
    its console, and note the R process uptime and id at that moment in
    `info` ({session name: {...}}, shared by every browser). A session whose
    wrapper cannot be written is marked in `prepared`, so its script is not
    run (the others still are). `start_at`: one start time for every
    session, or {session name: start time} (start_schedule())."""

    def __init__(self, script_path, run_dir, session_folders, suffix, shared_dir, start_at,
                 iterations=1, think_time_s=0.0):
        self.script_path, self.run_dir, self.session_folders = script_path, run_dir, session_folders
        self.suffix, self.shared_dir, self.start_at = suffix, shared_dir, start_at
        self.iterations, self.think_time_s = iterations, think_time_s
        self.info = {}

    def __call__(self, launched, prepared):
        uptimes = []
        for session in launched:
            name = session.session_name
            if session.error or name not in prepared or prepared[name][1] is not None:
                continue
            folder = self.session_folders[name]
            session_dir = posixpath.join(self.run_dir, folder)
            path = wrapper_path(self.run_dir, folder)
            start_at = self.start_at.get(name) if isinstance(self.start_at, dict) else self.start_at
            code = wrapper_source(self.script_path, name, self.suffix, session_dir, self.shared_dir, start_at,
                                  self.iterations, self.think_time_s)
            r_expr = ("{dir.create(%s, showWarnings=FALSE, recursive=TRUE); writeLines(%s, %s); "
                      "paste(file.exists(%s), sprintf('%%.1f', proc.time()[['elapsed']]), Sys.getpid(), sep='|')}"
                      % (r_string(self.shared_dir or session_dir), r_string(code), r_string(path), r_string(path)))
            try:
                written, uptime, pid = read_console_value(session.page, r_expr, timeout_ms=30000).split("|")
                if written != "TRUE":
                    raise RuntimeError("%s was not written" % path)
                self.info[name] = {"uptime_at_prepare": float(uptime), "pid_at_prepare": pid,
                                   "prepared_at": time.time(), "restarted": False}
                uptimes.append((name, float(uptime)))
                log_step("Wrapper %s written for session %s (R up %ss, pid %s)", path, name, uptime, pid)
            except Exception as exc:
                log_step("Could not write the wrapper for session %s: %s", name, " ".join(str(exc).split())[:200])
                prepared[name] = (prepared[name][0], exc)
        for name in flag_restarts(uptimes):
            self.info[name]["restarted"] = True
            log_step("WARNING: session %s: R has been up only %.0fs, less than the session launched after it - "
                     "it was restarted (suspended and resumed?) before its script started",
                     name, self.info[name]["uptime_at_prepare"])


# --- Reading the timings back -------------------------------------------------------
def parse_timing_csv(text):
    """The rows of a timing CSV written by the wrapper: start/end and R
    uptime as floats, iteration as int."""
    rows = []
    for row in csv.DictReader(io.StringIO(text or "")):
        try:
            row["start"], row["end"] = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            row["iteration"] = int(float(row.get("iteration") or 1))
        except ValueError:
            row["iteration"] = 1
        for key in ("r_uptime_at_submit", "scheduled_start"):
            try:
                row[key] = float(row[key])
            except (KeyError, TypeError, ValueError):
                row[key] = None
        rows.append(row)
    return rows


def read_server_timing(page, record):
    """The after_run hook: read the session's timing.csv (its working
    directory is its session folder) and the mrgsolve run summary into
    `record`. Every run's row goes to record["server_rows"]; the client times
    move to client_started/client_ended and started/ended become the server
    times of the first start and last end, so the existing timing CSV,
    summary and charts show server timings. Failures are logged, never
    raised."""
    name = record["session_name"]
    try:
        rows = parse_timing_csv(read_text_file(page, TIMING_FILE_NAME))
        if rows:
            record["server_rows"] = rows
            record["client_started"], record["client_ended"] = record.get("started"), record.get("ended")
            record["started"] = min(r["start"] for r in rows)
            record["ended"] = max(r["end"] for r in rows)
            record["host"], record["pid"] = rows[0].get("host"), rows[0].get("pid")
            record["r_uptime_at_submit"] = rows[0].get("r_uptime_at_submit")
            script = rows[0].get("script") or "mrgsolve script"
            for row in rows:
                delay = ("" if row.get("scheduled_start") is None
                         else ", %.2fs after its scheduled start" % (row["start"] - row["scheduled_start"]))
                log_step("R script %s (run %d) in session %s: started %s, ended %s, %.2fs, %s%s",
                         script, row["iteration"], name, format_epoch(row["start"]), format_epoch(row["end"]),
                         row["end"] - row["start"], row.get("status") or "?", delay)
            log_step("Server timing of session %s: %d run(s), %.2fs from first start to last end, on %s (pid %s)",
                     name, len(rows), record["ended"] - record["started"], record["host"], record["pid"])
        else:
            log_step("WARNING: no %s in the folder of session %s - keeping the client times", TIMING_FILE_NAME, name)
    except Exception as exc:
        log_step("WARNING: could not read the server timing of session %s: %s", name,
                 " ".join(str(exc).split())[:200])
    read_mrgsolve_summary(page, record)


def read_group_timings(page, shared_dir):
    """Every row in `shared_dir` (all workspaces' sessions), read through the
    console of `page`."""
    # The console only keeps its last lines, so the rows are combined into one
    # file in R and read back 50 lines at a time. (.txt: not picked up as a
    # session's .csv by the next read.)
    combined = posixpath.join(shared_dir, "_all_sessions.txt")
    count = int(read_console_value(
        page, "{f <- list.files(%s, pattern='\\\\.csv$', full.names=TRUE); "
              "d <- if (length(f)) do.call(rbind, lapply(f, read.csv, stringsAsFactors=FALSE, colClasses='character')) "
              "else data.frame(); write.csv(d, %s, row.names=FALSE); format(nrow(d))}"
              % (r_string(shared_dir), r_string(combined)), timeout_ms=120000))
    if not count:
        return []
    lines = []
    chunk = 50
    total_lines = count + 1  # header
    for first in range(1, total_lines + 1, chunk):
        # One row per console line: joined into one line, the rows passed
        # RStudio's console line length limit and the read timed out.
        lines += read_text_lines(page, combined, first, min(first + chunk - 1, total_lines))
    return parse_timing_csv("\n".join(lines))


def records_to_rows(runs, info=None, suffix=""):
    """Every run (iteration) of the records with a server timing, as
    concurrency_stats() rows, with the restart flag from the WrapperWriter
    `info`: R restarted before prepare, or a different R process at the run
    than at prepare."""
    rows = []
    for record in runs:
        noted = (info or {}).get(record["session_name"], {})
        for row in record.get("server_rows") or []:
            restarted = noted.get("restarted") or (
                noted.get("pid_at_prepare") is not None and str(row.get("pid")) != str(noted["pid_at_prepare"]))
            rows.append(dict(row, suffix=row.get("suffix") or suffix, restarted=bool(restarted)))
    return rows


# --- Concurrency figures ----------------------------------------------------------------
def concurrency_stats(rows):
    """Figures showing how far the runs overlapped. `rows`: one per script
    run (iteration) with session, start/end (epoch seconds), status, host.
    Think time between a session's runs does not count as running.

    runs, sessions, ok, failed; peak_concurrent (most runs executing at the
    same moment) and peak_at; all_overlap_s (latest first start -> earliest
    first end: how long every session's first run was running at once; 0 if
    they never all overlapped); start_spread_s (first -> last first start);
    first_start/last_start/first_end/last_end; wall_clock_s (first start ->
    last end); min/avg/max run time of the ok runs; sessions_per_host.
    """
    timed = [r for r in rows if r.get("start") is not None and r.get("end") is not None]
    ok = [r for r in timed if (r.get("status") or "") == "ok"]
    sessions = {}
    for r in timed:
        sessions.setdefault(r.get("session"), []).append(r)
    stats = {"runs": len(rows), "sessions": len(sessions), "ok": len(ok), "failed": len(rows) - len(ok)}
    if not timed:
        return dict(stats, peak_concurrent=0, all_overlap_s=0.0)
    # sweep: an end at the same moment as a start is counted first (no false overlap)
    events = sorted([(r["start"], 1) for r in timed] + [(r["end"], -1) for r in timed], key=lambda e: (e[0], e[1]))
    running = peak = 0
    peak_at = None
    for at, step in events:
        running += step
        if running > peak:
            peak, peak_at = running, at
    firsts = [min(runs, key=lambda r: r["start"]) for runs in sessions.values()]
    first_starts, first_ends = [r["start"] for r in firsts], [r["end"] for r in firsts]
    # Staggered start: how far each first run started after its scheduled time
    delays = sorted(r["start"] - r["scheduled_start"] for r in firsts if r.get("scheduled_start") is not None)
    ordered = sorted(first_starts)
    gaps = sorted(b - a for a, b in zip(ordered, ordered[1:]))
    durations = sorted(r["end"] - r["start"] for r in ok)
    hosts = {}
    for name, runs in sessions.items():
        host = runs[0].get("host") or "?"
        hosts[host] = hosts.get(host, 0) + 1
    stats.update({
        "peak_concurrent": peak,
        "peak_at": format_epoch(peak_at),
        "all_overlap_s": round(max(0.0, min(first_ends) - max(first_starts)), 2),
        "start_spread_s": round(max(first_starts) - min(first_starts), 2),
        "first_start": format_epoch(min(first_starts)),
        "last_start": format_epoch(max(first_starts)),
        "first_end": format_epoch(min(r["end"] for r in timed)),
        "last_end": format_epoch(max(r["end"] for r in timed)),
        "wall_clock_s": round(max(r["end"] for r in timed) - min(first_starts), 2),
        "min_s": round(durations[0], 2) if durations else None,
        "avg_s": round(sum(durations) / len(durations), 2) if durations else None,
        "max_s": round(durations[-1], 2) if durations else None,
        "sessions_per_host": dict(sorted(hosts.items())),
        # spacing between consecutive first starts (3s for a 200-session, 600s ramp-up)
        "median_start_gap_s": round(gaps[len(gaps) // 2], 2) if gaps else None,
        "max_start_gap_s": round(gaps[-1], 2) if gaps else None,
        "scheduled_sessions": len(delays),
        "max_start_delay_s": round(delays[-1], 2) if delays else None,
        "avg_start_delay_s": round(sum(delays) / len(delays), 2) if delays else None,
    })
    return stats


def describe_stats(stats):
    """One-paragraph summary of concurrency_stats()."""
    if not stats.get("peak_concurrent"):
        return "%d run(s), none with a server timing" % stats["runs"]
    schedule = ""
    if stats.get("scheduled_sessions"):
        schedule = ("; staggered start: median gap %ss between starts (max %ss), started after schedule by "
                    "avg %ss / max %ss" % (stats["median_start_gap_s"], stats["max_start_gap_s"],
                                           stats["avg_start_delay_s"], stats["max_start_delay_s"]))
    return ("%d run(s) of %d session(s) (%d ok, %d failed): peak %d running at once (at %s); "
            "every session's first run ran together for %.2fs; first starts spread over %.2fs (%s -> %s); "
            "first start to last end %.2fs; per run min %ss / avg %ss / max %ss; %d host(s)"
            % (stats["runs"], stats["sessions"], stats["ok"], stats["failed"], stats["peak_concurrent"],
               stats["peak_at"], stats["all_overlap_s"], stats["start_spread_s"], stats["first_start"],
               stats["last_start"], stats["wall_clock_s"], stats["min_s"], stats["avg_s"], stats["max_s"],
               len(stats["sessions_per_host"]))) + schedule


def idle_summary(rows, idle_warn_s):
    """Idle and restart figures per session (its first run's row): how long
    R had been up when the wrapper was submitted - an upper bound on the
    time the session went without its script - against `idle_warn_s`, and
    how many sessions' R was restarted (suspended and resumed?)."""
    firsts = {}
    for r in rows:
        if r.get("session") not in firsts or r["iteration"] < firsts[r["session"]]["iteration"]:
            firsts[r.get("session")] = r
    uptimes = {name: r.get("r_uptime_at_submit") for name, r in firsts.items()
               if r.get("r_uptime_at_submit") is not None}
    over = sorted(name for name, up in uptimes.items() if idle_warn_s and up > idle_warn_s)
    restarted = sorted(name for name, r in firsts.items() if r.get("restarted"))
    return {
        "sessions": len(firsts),
        "max_uptime_at_submit_s": round(max(uptimes.values()), 1) if uptimes else None,
        "idle_warn_s": idle_warn_s,
        "over_idle_warn": over,
        "restarted": restarted,
    }


def describe_idle(summary):
    """One line from idle_summary()."""
    return ("idle before the script: longest R uptime at submit %ss (limit %ss); %d session(s) over the limit%s; "
            "%d session(s) with R restarted (suspended/resumed?)%s"
            % (summary["max_uptime_at_submit_s"], summary["idle_warn_s"], len(summary["over_idle_warn"]),
               " (%s)" % ", ".join(summary["over_idle_warn"]) if summary["over_idle_warn"] else "",
               len(summary["restarted"]),
               " (%s)" % ", ".join(summary["restarted"]) if summary["restarted"] else ""))


# --- Report ---------------------------------------------------------------------------
def write_concurrency_report(rows, stats, label, title, idle_warn_s=None):
    """evidence/mrgsolve_concurrency_<label>.csv (one row per script run, by
    start time, with the idle and restart columns), .json (the stats) and
    .png (timeline: one line per session, a bar per run, the window in which
    every first run was running shaded), all attached to Allure. Returns the
    paths written."""
    folder = evidence_dir()
    csv_path = os.path.join(folder, "mrgsolve_concurrency_%s.csv" % label)
    table = [["Session", "Suffix", "Iteration", "Host", "PID", "Scheduled Start", "Server Start", "Start Delay (s)",
              "Server End", "Time Taken (s)", "Status", "R Uptime At Submit (s)", "Idle Warning", "R Restarted"]]
    for r in sorted(rows, key=lambda r: r["start"]):
        uptime = r.get("r_uptime_at_submit")
        scheduled = r.get("scheduled_start") if r.get("iteration", 1) == 1 else None
        table.append([
            r.get("session"), r.get("suffix", ""), r.get("iteration", 1), r.get("host"), r.get("pid"),
            format_epoch(scheduled), format_epoch(r["start"]),
            "" if scheduled is None else "%.2f" % (r["start"] - scheduled),
            format_epoch(r["end"]), "%.2f" % (r["end"] - r["start"]), r.get("status"),
            "" if uptime is None else "%.1f" % uptime,
            "over %ss" % idle_warn_s if (idle_warn_s and uptime is not None and uptime > idle_warn_s) else "",
            "yes" if r.get("restarted") else "",
        ])
    write_csv_report(csv_path, table)
    json_path = os.path.join(folder, "mrgsolve_concurrency_%s.json" % label)
    write_json_report(json_path, stats)
    attach_allure_file(json_path)
    paths = [csv_path, json_path]
    png_path = _draw_timeline(rows, stats, os.path.join(folder, "mrgsolve_concurrency_%s.png" % label), title)
    if png_path:
        attach_allure_file(png_path)
        paths.append(png_path)
    return paths


def _draw_timeline(rows, stats, path, title):
    """One line per session (by first start), a bar per run from its server
    start to end (seconds after the first start), failed runs in red, the
    window in which every first run was running shaded. Returns `path`, or
    None without matplotlib or rows."""
    if not rows:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        log_step("WARNING: concurrency timeline not drawn (matplotlib missing): %s", exc)
        return None
    sessions = {}
    for r in rows:
        sessions.setdefault(r.get("session"), []).append(r)
    order = sorted(sessions, key=lambda name: min(r["start"] for r in sessions[name]))
    t0 = min(r["start"] for r in rows)
    height = min(40.0, max(3.0, 0.12 * len(order) + 1.5))
    fig, ax = plt.subplots(figsize=(12, height))
    for i, name in enumerate(order):
        for r in sessions[name]:
            ok = (r.get("status") or "") == "ok"
            ax.barh(i, r["end"] - r["start"], left=r["start"] - t0, height=0.8,
                    color="#4C78A8" if ok else "#E45756")
    overlap = stats.get("all_overlap_s") or 0
    if overlap > 0:
        last_first_start = max(min(r["start"] for r in runs) for runs in sessions.values()) - t0
        ax.axvspan(last_first_start, last_first_start + overlap, color="#54A24B", alpha=0.15,
                   label="all %d first runs running (%.0fs)" % (len(order), overlap))
        ax.legend(loc="lower right")
    ax.set_xlabel("seconds after the first start (server clock)")
    ax.set_ylabel("session (by first start)")
    ax.set_yticks([])
    ax.invert_yaxis()
    ax.set_title("%s\npeak %d running at once, first starts spread over %.1fs"
                 % (title, stats.get("peak_concurrent", 0), stats.get("start_spread_s") or 0))
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)
    return path
