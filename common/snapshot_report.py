"""Tabular report of the project snapshot timing runs (tests/test_project_snapshot_time.py
and tests/test_initial_50gb.py).

Reads every run in evidence/snapshot_times.csv (one row per scenario run), adds the
details each run saved in evidence/runs/<run>_<test file>/snapshot_time_*.json (poll
history, queue wait, step times, list entry), and writes:

    evidence/snapshot_report.html   styled tables, for sharing / Allure
    evidence/snapshot_report.md     the same tables in Markdown
    evidence/snapshot_report.png    the timing tables as one image (needs matplotlib)

Sections: overview, key findings, snapshot time by data size (SNAPSHOT_DATA_GB with
several sizes), summary by size and scenario, committing and uploading, latest run, all
runs, step breakdown, test data, failures, method and settings.

The test rewrites the report after every scenario (record_snapshot_result). By hand:

    python -m common.snapshot_report
"""
import csv
import glob
import html
import json
import os
import re
from datetime import datetime, timezone
from statistics import mean
from typing import Optional
from urllib.parse import urlparse

from config.config import EVIDENCE_ROOT, env

RESULTS_CSV = os.path.join(EVIDENCE_ROOT, "snapshot_times.csv")
RUNS_DIR = os.path.join(EVIDENCE_ROOT, "runs")
REPORT_HTML = os.path.join(EVIDENCE_ROOT, "snapshot_report.html")
REPORT_MD = os.path.join(EVIDENCE_ROOT, "snapshot_report.md")
REPORT_PNG = os.path.join(EVIDENCE_ROOT, "snapshot_report.png")

# Results from before the "test" column were all the cycle test's
DEFAULT_TEST = "test_project_snapshot_time"
INITIAL_TEST = "test_initial_50gb"
# A run's kind: its scenario, or "initial" for the initial 50 GB test's add
SCENARIO_ORDER = {"initial": -1, "add": 0, "modify": 1, "remove": 2}
SCENARIO_LABEL = {"initial": "Initial load", "add": "Add data", "modify": "Modify data", "remove": "Remove data"}
# The PNG shows these sections ("Latest run (...)" by its prefix); All runs at most PNG_MAX_RUNS rows, newest
PNG_SECTIONS = ("Snapshot time by data size", "Summary by size and scenario", "Committing and uploading",
                "Latest run", "All runs")
PNG_MAX_RUNS = 40
# The snapshot job's steps, in order (API 2's "step", step_number/step_count 6): 1 waiting, 2 cloning,
# 3 committing, 4 uploading, 5 saving, 6 checksum - the last poll's step once the state is "succeeded".
# Saving is short and usually falls between two 10 s polls, so it seldom gets its own time.
STEP_ORDER = ("waiting", "cloning", "committing", "uploading", "saving", "checksum")
SUCCESS = {"success", "succeeded", "completed"}
# The longest steps, reported on their own
CRITICAL_STEPS = ("committing", "uploading")


# -- reading -------------------------------------------------------------------

def _num(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def size_gb(text) -> Optional[float]:
    """The snapshot list's size ("2.43 GB", "515.73 MB") in GB; None if unreadable."""
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?B)\s*$", str(text or ""), re.I)
    if not m:
        return None
    factor = {"B": 1e-9, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}[m.group(2).upper()]
    return float(m.group(1)) * factor


def _size_key(value) -> Optional[str]:
    """2.0 / "2" / "2.0" -> "2"; None when there is no size."""
    number = _num(value)
    return "%g" % number if number is not None else None


def _run_jsons() -> dict:
    """{(run_stamp, test, scenario, size): saved result} from every
    evidence/runs/<run stamp>_<test file>/snapshot_time*.json (size None for
    results saved before the size sequence)."""
    found = {}
    for path in glob.glob(os.path.join(RUNS_DIR, "*_test_*", "snapshot_time*.json")):
        stamp, _, test = os.path.basename(os.path.dirname(path)).partition("_")
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        found[(stamp, data.get("test") or test, data.get("scenario") or "add",
               _size_key(data.get("target_gb")))] = data
    return found


def load_runs() -> list:
    """Every CSV row merged with its saved JSON, oldest first, add/modify/remove
    within a run. Numbers are floats (None when missing)."""
    if not os.path.exists(RESULTS_CSV):
        return []
    with open(RESULTS_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    saved = _run_jsons()
    runs = []
    for row in rows:
        scenario = row.get("scenario") or "add"
        test = row.get("test") or DEFAULT_TEST
        key = (row.get("run_stamp"), test, scenario)
        detail = saved.get(key + (_size_key(row.get("target_gb")),)) or saved.get(key + (None,), {})
        run = dict(detail)
        run.update({k: v for k, v in row.items() if v not in (None, "")})
        run["scenario"], run["test"] = scenario, test
        run["kind"] = "initial" if test == INITIAL_TEST and scenario == "add" else scenario
        for key in ("target_gb", "data_gb", "data_percent", "data_files", "data_seconds", "queue_wait_s",
                    "snapshot_duration_s",
                    "client_duration_s", "polls", "poll_interval_s", "data_bytes", "created_snapshot_id"):
            run[key] = _num(run.get(key))
        if run.get("queue_wait_s") is None:
            run["queue_wait_s"] = _queue_wait(run)
        run["steps"] = run.get("step_durations_s") or _step_durations(run.get("poll_history"))
        run["size_gb"] = size_gb(run.get("snapshot_size"))
        run["ok"] = str(run.get("status", "")).lower() in SUCCESS
        runs.append(run)
    # in the order they ran: by run, then by trigger time (size by size), add/modify/remove
    runs.sort(key=lambda r: (r.get("run_stamp", ""), r.get("triggered_at_utc") or "",
                             SCENARIO_ORDER.get(r["kind"], 9)))
    _add_size_deltas(runs)
    return runs


def _parse_time(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _queue_wait(run) -> Optional[float]:
    status = run.get("last_status_response") or {}
    start, req = _parse_time(status.get("started_at")), _parse_time(status.get("requested_at"))
    return round((start - req).total_seconds(), 1) if start and req else None


def _step_durations(history) -> dict:
    seen = []
    for entry in history or []:
        step = entry.get("step")
        if step and (not seen or seen[-1][0] != step):
            seen.append((step, entry.get("elapsed_s") or 0.0))
    return {step: round(seen[i + 1][1] - at, 1) for i, (step, at) in enumerate(seen[:-1])}


def _add_size_deltas(runs):
    """size_delta_gb: the listed size minus the previous snapshot's (snapshot_id - 1), when it
    is one of these runs - a snapshot someone else took in between would make it meaningless."""
    by_id = {int(r["created_snapshot_id"]): r for r in runs if r.get("created_snapshot_id") and r["size_gb"]}
    for r in runs:
        r["size_delta_gb"] = None
        sid = r.get("created_snapshot_id")
        prev = by_id.get(int(sid) - 1) if sid else None
        if prev and r["size_gb"] is not None:
            r["size_delta_gb"] = round(r["size_gb"] - prev["size_gb"], 2)


# -- formatting ----------------------------------------------------------------

def secs(value) -> str:
    """75.69 -> '75.7 s (1:16)'; '' when missing."""
    if value is None:
        return ""
    value = float(value)
    return "%.1f s (%d:%02d)" % (value, value // 60, round(value % 60)) if value >= 60 else "%.1f s" % value


def gb(value, signed=False) -> str:
    if value is None:
        return ""
    return ("%+.2f GB" if signed else "%.2f GB") % value


def change_text(run) -> str:
    """'+1.00 GB, 581 files' / '10% = 0.10 GB, 195 files' / '-1.00 GB, 582 files'."""
    action, size, files = run.get("data_action") or run["scenario"], run.get("data_gb"), run.get("data_files")
    files_text = ", %d files" % files if files else ""
    if size is None:
        return ""
    if action == "add":
        return "+%.2f GB%s" % (size, files_text)
    if action == "remove":
        return "-%.2f GB%s" % (size, files_text)
    pct = run.get("data_percent")
    return "%s%.2f GB changed%s" % ("%g%% = " % pct if pct else "", size, files_text)


def size_text(run) -> str:
    """The cycle's data size: '2 GB'; '' when unknown."""
    return "%g GB" % run["target_gb"] if run.get("target_gb") is not None else ""


def _groups(runs: list) -> list:
    """[(size, kind)] present in `runs`: sizes ascending, initial/add/modify/remove."""
    return sorted({(r.get("target_gb"), r["kind"]) for r in runs},
                  key=lambda g: (g[0] if g[0] is not None else -1, SCENARIO_ORDER.get(g[1], 9)))


def utc_short(value) -> str:
    t = _parse_time(value)
    return t.strftime("%Y-%m-%d %H:%M:%S UTC") if t else (value or "")


def stat(values, fn) -> Optional[float]:
    values = [v for v in values if v is not None]
    return fn(values) if values else None


# -- tables ----------------------------------------------------------------------

def _tables(runs: list) -> list:
    """[(section title, intro text, header, rows, note)] - the report, format-free."""
    failed = [r for r in runs if not r["ok"]]
    sections = []

    # Snapshot time by data size (only worth a table with more than one size)
    sizes = sorted({r["target_gb"] for r in runs if r.get("target_gb") is not None})
    if len(sizes) > 1:
        rows = []
        for size in sizes:
            row = [size_text({"target_gb": size})]
            for sc in ("add", "modify", "remove"):
                t = [r["snapshot_duration_s"] for r in runs if r["ok"] and r.get("target_gb") == size
                     and r["scenario"] == sc and r.get("snapshot_duration_s")]
                row.append(secs(stat(t, mean)))
            adds = [r for r in runs if r["ok"] and r.get("target_gb") == size and r["scenario"] == "add"
                    and r.get("snapshot_duration_s")]
            row.append("%.1f s/GB" % mean(r["snapshot_duration_s"] / size for r in adds) if adds else "")
            row.append(secs(stat([r["steps"].get("committing") for r in adds], mean)))
            row.append(secs(stat([r["steps"].get("uploading") for r in adds], mean)))
            rows.append(row)
        sections.append(("Snapshot time by data size", "Average snapshot time of each size's cycle "
                         "(SNAPSHOT_DATA_GB), successful runs only.",
                         ["Data size", "Add", "Modify", "Remove", "Add per GB", "Add: committing",
                          "Add: uploading"], rows,
                         "Add per GB = the add snapshot time / the size: flat means the snapshot time grows in "
                         "step with the data, falling means a fixed overhead dominates small sizes."))

    # Summary by size and scenario
    rows = []
    for size, sc in _groups(runs):
        all_sc = [r for r in runs if r["kind"] == sc and r.get("target_gb") == size]
        good = [r for r in all_sc if r["ok"]]
        times = [r["snapshot_duration_s"] for r in good]
        rate = [r["data_gb"] / (r["snapshot_duration_s"] / 60.0) for r in good
                if r.get("data_gb") and r.get("snapshot_duration_s")]
        rows.append([size_text({"target_gb": size}), SCENARIO_LABEL.get(sc, sc), len(all_sc), len(good),
                     len(all_sc) - len(good),
                     secs(stat(times, min)), secs(stat(times, mean)), secs(stat(times, max)),
                     secs(stat([r["queue_wait_s"] for r in good], mean)),
                     secs(stat([r["client_duration_s"] for r in good], mean)),
                     gb(stat([r["data_gb"] for r in good], mean)),
                     "%.2f GB/min" % stat(rate, mean) if rate else ""])
    sections.append(("Summary by size and scenario",
                     "Successful runs only for the times; failed runs are counted.",
                     ["Data size", "Scenario", "Runs", "Passed", "Failed", "Snapshot time min", "avg", "max",
                      "Queue wait avg", "Trigger to success avg", "Data changed avg", "Data changed per min"],
                     rows, "Snapshot time = finished_at - started_at from the snapshot status API (server side, "
                           "excludes the queue). Trigger to success = the test's own clock, from the create call to "
                           "the poll that saw success (includes the queue, up to one poll interval late)."))

    # Critical steps: committing and uploading take most of every snapshot
    rows = []
    for size, sc in _groups(runs):
        good = [r for r in runs if r["kind"] == sc and r.get("target_gb") == size and r["ok"]
                and r.get("snapshot_duration_s")]
        row = [size_text({"target_gb": size}), SCENARIO_LABEL.get(sc, sc), len(good)]
        for step in CRITICAL_STEPS:
            t = [r["steps"][step] for r in good if r["steps"].get(step)]
            row += [secs(stat(t, min)), secs(stat(t, mean)), secs(stat(t, max)),
                    "%d of %d" % (len(t), len(good)) if len(t) < len(good) else ""]
        both = [sum(r["steps"].get(s) or 0 for s in CRITICAL_STEPS) / r["snapshot_duration_s"] for r in good]
        row.append("%.0f%%" % (100 * mean(both)) if both else "")
        rows.append(row)
    if rows:
        sections.append((
            "Committing and uploading", "The two longest steps of the snapshot job (steps 3 and 4 of 6), "
            "successful runs only.",
            ["Data size", "Scenario", "Runs", "Committing min", "avg", "max", "Seen in", "Uploading min", "avg", "max",
             "Seen in", "Share of snapshot time"], rows,
            "Times come from the status polls, so each is accurate to one poll interval; lower "
            "SNAPSHOT_POLL_INTERVAL_S for finer times. \"Seen in\" counts the runs where a poll caught the step: "
            "a step shorter than one poll interval, or the last step before the job finished, gets no time."))

    # Latest cycle
    if runs:
        last_stamp = runs[-1].get("run_stamp")
        cycle = [r for r in runs if r.get("run_stamp") == last_stamp]
        rows = [[size_text(r), SCENARIO_LABEL.get(r["kind"], r["kind"]), r.get("status", ""), change_text(r),
                 secs(r.get("data_seconds")), r.get("snapshot_id", ""), r.get("created_snapshot_id") and
                 "%d" % r["created_snapshot_id"] or "", secs(r["queue_wait_s"]), secs(r.get("snapshot_duration_s")),
                 secs((r["steps"] or {}).get("committing")), secs((r["steps"] or {}).get("uploading")),
                 secs(r.get("client_duration_s")), r.get("snapshot_size", ""), gb(r["size_delta_gb"], True),
                 r.get("list_check", "")] for r in cycle]
        sections.append(("Latest run (%s)" % last_stamp, "", [
            "Data size", "Scenario", "Status", "Data change", "Data change took", "Job id", "Snapshot id", "Queue wait",
            "Snapshot time", "Committing", "Uploading", "Trigger to success", "Size in list", "Size change",
            "List check"], rows, ""))

    # All runs
    rows = [[r.get("run_stamp", ""), size_text(r), SCENARIO_LABEL.get(r["kind"], r["kind"]),
             r.get("status", ""), r.get("snapshot_id", ""),
             r.get("created_snapshot_id") and "%d" % r["created_snapshot_id"] or "",
             r.get("snapshot_version", ""), change_text(r), secs(r.get("data_seconds")), secs(r["queue_wait_s"]),
             secs(r.get("snapshot_duration_s")), secs(r.get("client_duration_s")),
             "%d" % r["polls"] if r.get("polls") else "", r.get("snapshot_size", ""), gb(r["size_delta_gb"], True),
             r.get("list_check", ""), utc_short(r.get("snapshot_started_at"))] for r in runs]
    sections.append(("All runs", "One row per scenario run, oldest first.", [
        "Run", "Data size", "Scenario", "Status", "Job id", "Snapshot id", "Version", "Data change", "Data change took",
        "Queue wait", "Snapshot time", "Trigger to success", "Polls", "Size in list", "Size change", "List check",
        "Started"], rows, ""))

    # Step breakdown
    step_names = [s for s in STEP_ORDER if any(s in (r["steps"] or {}) for r in runs)]
    step_names += sorted({s for r in runs for s in (r["steps"] or {}) if s not in step_names})
    rows = []
    for r in runs:
        if not r["steps"] and not r.get("poll_history"):
            continue
        # the job's last step: from its final status response when saved (a failed job may have moved on
        # after the polls stopped), else from the last poll that showed one
        final = r.get("last_status_response") or {}
        last = [e for e in (r.get("poll_history") or []) if e.get("step")]
        src = final if final.get("step") else (last[-1] if last else {})
        ended = "%s (%s/%s)" % (src["step"], src.get("step_number"), src.get("step_count")) if src else ""
        errors = len([e for e in (r.get("poll_history") or []) if e.get("error")])
        rows.append([r.get("run_stamp", ""), size_text(r), SCENARIO_LABEL.get(r["kind"], r["kind"]),
                     r.get("status", "")]
                    + [secs(r["steps"].get(s)) if s in r["steps"] else "" for s in step_names]
                    + [ended, errors or "", secs(r.get("snapshot_duration_s"))])
    if rows:
        sections.append(("Step breakdown", "How long the job spent in each step, from the status polls.",
                         ["Run", "Data size", "Scenario", "Status"] + [s.capitalize() for s in step_names]
                         + ["Ended at step", "Failed polls", "Snapshot time"], rows,
                         "Each step lasts from the first poll that showed it to the first poll that showed the next "
                         "step, so each is accurate to one poll interval (%s s). The step the job ended in has no "
                         "time of its own (it finished before the next poll)." %
                         ("/".join(sorted({"%g" % r["poll_interval_s"] for r in runs if r.get("poll_interval_s")}))
                          or "10")))

    # Test data
    rows = []
    for r in runs:
        if r.get("data_seconds") is None:
            continue
        rate = (r["data_bytes"] or (r["data_gb"] or 0) * 1e9) / 1e6 / r["data_seconds"] if r["data_seconds"] else None
        rows.append([r.get("run_stamp", ""), SCENARIO_LABEL.get(r["kind"], r["kind"]),
                     r.get("data_action") or r["scenario"], "%d" % r["data_files"] if r.get("data_files") else "",
                     "{:,.0f}".format(r["data_bytes"]) if r.get("data_bytes") else gb(r.get("data_gb")),
                     "%g%%" % r["data_percent"] if r.get("data_percent") else "", r.get("data_seed", ""),
                     secs(r["data_seconds"]), "%.0f MB/s" % rate if rate else ""])
    sections.append(("Test data", "What each scenario changed before its snapshot, on the server.",
                     ["Run", "Scenario", "Action", "Files", "Bytes", "Share", "Seed", "Took", "Rate"], rows,
                     "Add: common/make_client_shape.py (the client's file-size shape: ~97% of files under 512 KB, "
                     "~99% of bytes in large files). Modify: common/modify_test_data.py overwrites the share of the "
                     "bytes in place (files keep names and sizes; seed = which files). Remove: the data folder is "
                     "deleted."))

    # Failures
    rows = [[r.get("run_stamp", ""), SCENARIO_LABEL.get(r["kind"], r["kind"]), r.get("snapshot_id", ""),
             r.get("status", ""), utc_short(r.get("snapshot_started_at")), utc_short(r.get("snapshot_ended_at")),
             r.get("error", "")] for r in failed]
    sections.append(("Failures and incidents", "" if rows else "None.",
                     ["Run", "Scenario", "Job id", "Outcome", "Started", "Ended", "Error"], rows, ""))
    return sections


def _overview(runs: list) -> list:
    ok = [r for r in runs if r["ok"]]
    host = urlparse(env("API_BASE_URL") or "").netloc
    projects = sorted({str(r.get("project_id", "")) for r in runs if r.get("project_id")})
    first, last = (runs[0].get("run_stamp"), runs[-1].get("run_stamp")) if runs else ("", "")
    return [
        ("Project", ", ".join(projects) + (" (%s)" % env("SNAPSHOT_PROJECT_DIR") if env("SNAPSHOT_PROJECT_DIR")
                                           else "")),
        ("Snapshot API", host),
        ("Runs", "%d scenario runs: %d passed, %d failed" % (len(runs), len(ok), len(runs) - len(ok))),
        ("Tests", ", ".join("%s (%d)" % (t, len([r for r in runs if r["test"] == t]))
                            for t in sorted({r["test"] for r in runs}))),
        ("Period", "%s to %s" % (first, last)),
        ("Data folder", env("SNAPSHOT_DATA_DIR") or (env("SNAPSHOT_PROJECT_DIR") or "").rstrip("/")
         + "/snapshot_test_data"),
        ("Session", env("SNAPSHOT_SESSION_NAME") or "TEST_SNAPSHOT_NFR"),
        ("Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
    ]


def _findings(runs: list) -> list:
    """Plain-language findings computed from the runs."""
    ok = [r for r in runs if r["ok"]]
    out = []
    sizes = sorted({r["target_gb"] for r in ok if r.get("target_gb") is not None}) or [None]
    for size in sizes:
        avg = {}
        for sc in ("add", "modify", "remove"):
            t = [r["snapshot_duration_s"] for r in ok if r["scenario"] == sc and r.get("snapshot_duration_s")
                 and (size is None or r.get("target_gb") == size)]
            if t:
                avg[sc] = mean(t)
                out.append("%s%s: snapshot took %s on average over %d run(s) (range %s to %s)."
                           % ("%g GB, " % size if size is not None else "", SCENARIO_LABEL[sc], secs(avg[sc]),
                              len(t), secs(min(t)), secs(max(t))))
        if "add" in avg and "remove" in avg:
            out.append("%sRemoving data snapshots in about %.0f%% of the time adding the same amount takes."
                       % ("%g GB: " % size if size is not None else "", 100 * avg["remove"] / avg["add"]))
    adds = [r for r in ok if r["scenario"] == "add" and r.get("target_gb") and r.get("snapshot_duration_s")]
    add_sizes = sorted({r["target_gb"] for r in adds})
    if len(add_sizes) > 1:
        small, large = add_sizes[0], add_sizes[-1]
        t_small = mean(r["snapshot_duration_s"] for r in adds if r["target_gb"] == small)
        t_large = mean(r["snapshot_duration_s"] for r in adds if r["target_gb"] == large)
        out.append("Scaling: adding %g GB took %s on average, %g GB took %s - %.1fx the time for %gx the data."
                   % (small, secs(t_small), large, secs(t_large), t_large / t_small, large / small))
    timed = [r for r in ok if r["steps"].get("committing") and r.get("snapshot_duration_s")]
    if timed:
        commit = mean(r["steps"]["committing"] / r["snapshot_duration_s"] for r in timed)
        both = mean(sum(r["steps"].get(s) or 0 for s in CRITICAL_STEPS) / r["snapshot_duration_s"] for r in timed)
        uploads = [r["steps"]["uploading"] for r in timed if r["steps"].get("uploading")]
        out.append("Committing and uploading are the critical steps: together about %.0f%% of the snapshot time. "
                   "Committing alone is about %.0f%% (%s to %s)%s." % (
                       100 * both, 100 * commit, secs(min(r["steps"]["committing"] for r in timed)),
                       secs(max(r["steps"]["committing"] for r in timed)),
                       "; uploading %s to %s" % (secs(min(uploads)), secs(max(uploads))) if uploads else ""))
    queued = [r for r in ok if (r["queue_wait_s"] or 0) >= 5]
    if queued:
        out.append("%d run(s) waited in the queue before starting (up to %s); the snapshot time excludes it, "
                   "the trigger-to-success time includes it." % (len(queued), secs(max(r["queue_wait_s"]
                                                                                      for r in queued))))
    failed = [r for r in runs if not r["ok"]]
    for r in failed:
        out.append("Failure: job %s (%s%s, run %s) - %s." % (r.get("snapshot_id"), r["scenario"],
                                                          " " + size_text(r) if size_text(r) else "",
                                                          r.get("run_stamp"),
                                                        (r.get("error") or r.get("status", ""))[:400].rstrip(".")))
    return out


# -- writers -----------------------------------------------------------------------

_CSS = """
:root{--bg:#fff;--fg:#1d2330;--muted:#5d6676;--line:#dde1e8;--head:#f3f5f8;--ok:#1a7f37;--bad:#c62828;--accent:#2457a6}
@media (prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ef;--muted:#9aa3b2;--line:#2b313b;--head:#1c2027;
--ok:#4cc26b;--bad:#ff6b6b;--accent:#7aa7ff}}
body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
margin:0;padding:24px 16px}
main{max-width:1400px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 6px}
p.note,p.intro{color:var(--muted);margin:4px 0 8px;max-width:900px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap;vertical-align:top}
th{background:var(--head);font-weight:600}tr:last-child td{border-bottom:0}
td.wrapok{white-space:normal;min-width:320px}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad);font-weight:600}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 16px;margin:12px 0}dt{color:var(--muted)}dd{margin:0}
ul.findings li{margin:4px 0}
"""

_STATUS_WORDS = {"succeeded": "ok", "passed": "ok", "failed": "bad", "timeout": "bad", "error": "bad",
                 "list_check_failed": "bad", "cancelled": "bad"}


def _cell(value) -> str:
    text = html.escape(str(value if value is not None else ""))
    cls = _STATUS_WORDS.get(str(value).lower()) if value not in (None, "") else None
    if cls:
        return '<td class="%s">%s</td>' % (cls, text)
    if len(text) > 80:
        return '<td class="wrapok">%s</td>' % text
    return "<td>%s</td>" % text


def write_html(runs: list, path: str = REPORT_HTML) -> str:
    parts = ["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Snapshot timing report</title><style>%s</style></head><body><main>" % _CSS,
             "<h1>Project snapshot timing report</h1>",
             "<p class='intro'>How long a project snapshot takes after its data is added, modified or removed, "
             "measured by tests/test_project_snapshot_time.py.</p><dl>"]
    parts += ["<dt>%s</dt><dd>%s</dd>" % (html.escape(k), html.escape(str(v))) for k, v in _overview(runs)]
    parts.append("</dl><h2>Key findings</h2><ul class='findings'>")
    parts += ["<li>%s</li>" % html.escape(f) for f in _findings(runs)] or ["<li>No runs yet.</li>"]
    parts.append("</ul>")
    for title, intro, header, rows, note in _tables(runs):
        parts.append("<h2>%s</h2>" % html.escape(title))
        if intro:
            parts.append("<p class='intro'>%s</p>" % html.escape(intro))
        if rows:
            parts.append("<div class='wrap'><table><thead><tr>%s</tr></thead><tbody>"
                         % "".join("<th>%s</th>" % html.escape(h) for h in header))
            parts += ["<tr>%s</tr>" % "".join(_cell(c) for c in row) for row in rows]
            parts.append("</tbody></table></div>")
        if note:
            parts.append("<p class='note'>%s</p>" % html.escape(note))
    parts.append(_method_html())
    parts.append("</main></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    return path


def _method() -> list:
    return [
        ("Snapshot time", "finished_at - started_at in the snapshot status API's response "
                          "(GET /api/v1/projects/{id}/snapshot-jobs/{job}) once the job reports succeeded."),
        ("Trigger to success", "The test's monotonic clock from just before the create call (POST .../snapshots) "
                               "to the poll that saw success."),
        ("Queue wait", "started_at - requested_at in the status response: time the job waited before it started."),
        ("Polling", "Every %s s, up to %s s; HTTP errors are retried up to %s times in a row."
         % (env("SNAPSHOT_POLL_INTERVAL_S") or "10", env("SNAPSHOT_TIMEOUT_S") or "7200",
            env("SNAPSHOT_MAX_POLL_ERRORS") or "30")),
        ("List check", "After success the snapshot must be in the snapshot list (GET .../snapshots) with the "
                       "snapshot_id from the status response and the name, comment, is_major and project sent."),
        ("Size change", "Size in the list minus the previous snapshot's (snapshot_id - 1), when that snapshot "
                        "is one of these runs. Sizes are as the list rounds them."),
        ("Order", "Scenarios run add -> modify -> remove, each on the data the one before left."),
    ]


def _method_html() -> str:
    return ("<h2>Method</h2><dl>%s</dl>" % "".join("<dt>%s</dt><dd>%s</dd>" % (html.escape(k), html.escape(v))
                                                for k, v in _method()))


def _md_cell(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def write_markdown(runs: list, path: str = REPORT_MD) -> str:
    out = ["# Project snapshot timing report", "",
           "How long a project snapshot takes after its data is added, modified or removed, measured by "
           "`tests/test_project_snapshot_time.py`.", "", "| | |", "|---|---|"]
    out += ["| %s | %s |" % (_md_cell(k), _md_cell(v)) for k, v in _overview(runs)]
    out += ["", "## Key findings", ""] + ["- %s" % f for f in _findings(runs)]
    for title, intro, header, rows, note in _tables(runs):
        out += ["", "## %s" % title, ""]
        if intro:
            out += [intro, ""]
        if rows:
            out.append("| %s |" % " | ".join(_md_cell(h) for h in header))
            out.append("|%s|" % "|".join("---" for _ in header))
            out += ["| %s |" % " | ".join(_md_cell(c) for c in row) for row in rows]
        if note:
            out += ["", "_%s_" % note]
    out += ["", "## Method", ""] + ["- **%s**: %s" % (k, v) for k, v in _method()]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return path


def _png_sections(runs: list) -> list:
    """The timing sections of the report for the PNG, All runs cut to the newest PNG_MAX_RUNS."""
    out = []
    for title, _intro, header, rows, _note in _tables(runs):
        if not any(title == name or title.startswith(name + " (") for name in PNG_SECTIONS):
            continue
        if title == "All runs" and len(rows) > PNG_MAX_RUNS:
            title = "All runs (newest %d of %d)" % (PNG_MAX_RUNS, len(rows))
            rows = rows[-PNG_MAX_RUNS:]
        out.append((title, header, rows))
    return out


def write_png(runs: list, path: str = REPORT_PNG) -> Optional[str]:
    """The timing tables (by size, by size and scenario, committing and
    uploading, latest run, all runs) as one PNG; None when matplotlib is not
    installed or there are no runs."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    sections = [sec for sec in _png_sections(runs) if sec[2]]
    if not sections:
        return None
    # inches per character / table row / section title / space under a table
    char_in, row_in, title_in, gap_in = 0.074, 0.24, 0.45, 0.3

    def widths(header, rows):
        return [max([len(str(h))] + [len(str(r[i])) for r in rows]) + 3 for i, h in enumerate(header)]

    table_w = [sum(widths(h, r)) * char_in for _, h, r in sections]
    fig_w = max(12.0, max(table_w) + 0.6)
    heights = [title_in + (len(r) + 1) * row_in + gap_in for _, _, r in sections]
    fig = plt.figure(figsize=(fig_w, sum(heights) + 1.0), dpi=110)
    fig.patch.set_facecolor("white")
    ok = [r for r in runs if r["ok"]]
    fig.text(0.3 / fig_w, 1 - 0.35 / (sum(heights) + 1.0), "Project snapshot timings", fontsize=15,
             fontweight="bold", color="#1d2330", va="top")
    fig.text(0.3 / fig_w, 1 - 0.68 / (sum(heights) + 1.0),
             "Project %s  |  %d runs: %d passed, %d failed  |  generated %s" % (
                 ", ".join(sorted({str(r.get("project_id")) for r in runs if r.get("project_id")})), len(runs),
                 len(ok), len(runs) - len(ok), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")),
             fontsize=9, color="#5d6676", va="top")
    total_h, used = sum(heights) + 1.0, 1.0  # inches; the first inch holds the title
    status_colors = {"ok": "#1a7f37", "bad": "#c62828"}
    for (title, header, rows), h, w in zip(sections, heights, table_w, strict=True):
        ax = fig.add_axes([0.3 / fig_w, (total_h - used - h) / total_h, (fig_w - 0.6) / fig_w, h / total_h])
        used += h
        ax.axis("off")
        ax.text(0, 1 - 0.12 / h, title, fontsize=11, fontweight="bold", color="#1d2330", va="top",
                transform=ax.transAxes)
        col_w = widths(header, rows)
        frac = w / (fig_w - 0.6)
        table = ax.table(cellText=[[str(c) for c in r] for r in rows], colLabels=header,
                         colWidths=[c / sum(col_w) * frac for c in col_w], cellLoc="left", colLoc="left",
                         bbox=[0, gap_in / h, frac, 1 - (title_in + gap_in) / h])
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        for (row, _col), cell in table.get_celld().items():
            cell.set_edgecolor("#dde1e8")
            cell.set_linewidth(0.6)
            if row == 0:
                cell.set_facecolor("#f3f5f8")
                cell.get_text().set_fontweight("bold")
            else:
                cell.set_facecolor("white" if row % 2 else "#fafbfc")
                word = _STATUS_WORDS.get(str(cell.get_text().get_text()).lower())
                if word:
                    cell.get_text().set_color(status_colors[word])
                    cell.get_text().set_fontweight("bold")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig.savefig(path, facecolor="white")
    plt.close(fig)
    return path


def write_report() -> tuple:
    """Write the HTML, Markdown and PNG reports from all runs; (html path,
    md path, png path or None when no PNG could be made)."""
    runs = load_runs()
    return write_html(runs), write_markdown(runs), write_png(runs)


if __name__ == "__main__":
    for p in write_report():
        if p:
            print(p)
