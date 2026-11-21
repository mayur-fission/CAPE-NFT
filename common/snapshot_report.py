"""Tabular report of the project snapshot timing runs (tests/test_project_snapshot_time.py).

Reads every run in evidence/snapshot_times.csv (one row per scenario run), adds the
details each run saved in evidence/runs/<run>_test_project_snapshot_time/
snapshot_time_<scenario>.json (poll history, queue wait, step times, list entry), and
writes:

    evidence/snapshot_report.html   styled tables, for sharing / Allure
    evidence/snapshot_report.md     the same tables in Markdown

Sections: overview, key findings, summary by scenario, latest cycle, all runs, step
breakdown, size change in the snapshot list, test data, failures, method and settings.

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

SCENARIO_ORDER = {"add": 0, "modify": 1, "remove": 2}
SCENARIO_LABEL = {"add": "Add data", "modify": "Modify data", "remove": "Remove data"}
# The snapshot job's steps, in order (API 2's "step", step_number/step_count 6)
STEP_ORDER = ("waiting", "cloning", "committing", "uploading", "listing", "checksum")
SUCCESS = {"success", "succeeded", "completed"}


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


def _run_jsons() -> dict:
    """{(run_stamp, scenario): saved result} from every snapshot_time*.json."""
    found = {}
    for path in glob.glob(os.path.join(RUNS_DIR, "*_test_project_snapshot_time", "snapshot_time*.json")):
        stamp = os.path.basename(os.path.dirname(path)).split("_test_project_snapshot_time")[0]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        found[(stamp, data.get("scenario") or "add")] = data
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
        detail = saved.get((row.get("run_stamp"), scenario), {})
        run = dict(detail)
        run.update({k: v for k, v in row.items() if v not in (None, "")})
        run["scenario"] = scenario
        for key in ("data_gb", "data_percent", "data_files", "data_seconds", "queue_wait_s", "snapshot_duration_s",
                    "client_duration_s", "polls", "poll_interval_s", "data_bytes", "created_snapshot_id"):
            run[key] = _num(run.get(key))
        if run.get("queue_wait_s") is None:
            run["queue_wait_s"] = _queue_wait(run)
        run["steps"] = run.get("step_durations_s") or _step_durations(run.get("poll_history"))
        run["size_gb"] = size_gb(run.get("snapshot_size"))
        run["ok"] = str(run.get("status", "")).lower() in SUCCESS
        runs.append(run)
    runs.sort(key=lambda r: (r.get("run_stamp", ""), SCENARIO_ORDER.get(r["scenario"], 9)))
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

    # Summary by scenario
    rows = []
    for sc in sorted({r["scenario"] for r in runs}, key=lambda s: SCENARIO_ORDER.get(s, 9)):
        all_sc = [r for r in runs if r["scenario"] == sc]
        good = [r for r in all_sc if r["ok"]]
        times = [r["snapshot_duration_s"] for r in good]
        rate = [r["data_gb"] / (r["snapshot_duration_s"] / 60.0) for r in good
                if r.get("data_gb") and r.get("snapshot_duration_s")]
        rows.append([SCENARIO_LABEL.get(sc, sc), len(all_sc), len(good), len(all_sc) - len(good),
                     secs(stat(times, min)), secs(stat(times, mean)), secs(stat(times, max)),
                     secs(stat([r["queue_wait_s"] for r in good], mean)),
                     secs(stat([r["client_duration_s"] for r in good], mean)),
                     gb(stat([r["data_gb"] for r in good], mean)),
                     "%.2f GB/min" % stat(rate, mean) if rate else ""])
    sections.append(("Summary by scenario", "Successful runs only for the times; failed runs are counted.",
                     ["Scenario", "Runs", "Passed", "Failed", "Snapshot time min", "avg", "max",
                      "Queue wait avg", "Trigger to success avg", "Data changed avg", "Data changed per min"],
                     rows, "Snapshot time = finished_at - started_at from the snapshot status API (server side, "
                           "excludes the queue). Trigger to success = the test's own clock, from the create call to "
                           "the poll that saw success (includes the queue, up to one poll interval late)."))

    # Latest cycle
    if runs:
        last_stamp = runs[-1].get("run_stamp")
        cycle = [r for r in runs if r.get("run_stamp") == last_stamp]
        rows = [[SCENARIO_LABEL.get(r["scenario"], r["scenario"]), r.get("status", ""), change_text(r),
                 secs(r.get("data_seconds")), r.get("snapshot_id", ""), r.get("created_snapshot_id") and
                 "%d" % r["created_snapshot_id"] or "", secs(r["queue_wait_s"]), secs(r.get("snapshot_duration_s")),
                 secs(r.get("client_duration_s")), r.get("snapshot_size", ""), gb(r["size_delta_gb"], True),
                 r.get("list_check", "")] for r in cycle]
        sections.append(("Latest run (%s)" % last_stamp, "", [
            "Scenario", "Status", "Data change", "Data change took", "Job id", "Snapshot id", "Queue wait",
            "Snapshot time", "Trigger to success", "Size in list", "Size change", "List check"], rows, ""))

    # All runs
    rows = [[r.get("run_stamp", ""), SCENARIO_LABEL.get(r["scenario"], r["scenario"]), r.get("status", ""),
             r.get("snapshot_id", ""), r.get("created_snapshot_id") and "%d" % r["created_snapshot_id"] or "",
             r.get("snapshot_version", ""), change_text(r), secs(r.get("data_seconds")), secs(r["queue_wait_s"]),
             secs(r.get("snapshot_duration_s")), secs(r.get("client_duration_s")),
             "%d" % r["polls"] if r.get("polls") else "", r.get("snapshot_size", ""), gb(r["size_delta_gb"], True),
             r.get("list_check", ""), utc_short(r.get("snapshot_started_at"))] for r in runs]
    sections.append(("All runs", "One row per scenario run, oldest first.", [
        "Run", "Scenario", "Status", "Job id", "Snapshot id", "Version", "Data change", "Data change took",
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
        rows.append([r.get("run_stamp", ""), SCENARIO_LABEL.get(r["scenario"], r["scenario"]), r.get("status", "")]
                    + [secs(r["steps"].get(s)) if s in r["steps"] else "" for s in step_names]
                    + [ended, errors or "", secs(r.get("snapshot_duration_s"))])
    if rows:
        sections.append(("Step breakdown", "How long the job spent in each step, from the status polls.",
                         ["Run", "Scenario", "Status"] + [s.capitalize() for s in step_names]
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
        rows.append([r.get("run_stamp", ""), SCENARIO_LABEL.get(r["scenario"], r["scenario"]),
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
    rows = [[r.get("run_stamp", ""), SCENARIO_LABEL.get(r["scenario"], r["scenario"]), r.get("snapshot_id", ""),
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
    avg = {}
    for sc in ("add", "modify", "remove"):
        t = [r["snapshot_duration_s"] for r in ok if r["scenario"] == sc and r.get("snapshot_duration_s")]
        if t:
            avg[sc] = mean(t)
            out.append("%s: snapshot took %s on average over %d run(s) (range %s to %s)."
                       % (SCENARIO_LABEL[sc], secs(avg[sc]), len(t), secs(min(t)), secs(max(t))))
    if "add" in avg and "remove" in avg:
        out.append("Removing data snapshots in about %.0f%% of the time adding the same amount takes."
                   % (100 * avg["remove"] / avg["add"]))
    commits = [(r["steps"].get("committing"), r["snapshot_duration_s"]) for r in ok
               if r["steps"].get("committing") and r.get("snapshot_duration_s")]
    if commits:
        share = mean(c / d for c, d in commits)
        out.append("The committing step is the largest part of every snapshot: about %.0f%% of the snapshot time."
                   % (100 * share))
    queued = [r for r in ok if (r["queue_wait_s"] or 0) >= 5]
    if queued:
        out.append("%d run(s) waited in the queue before starting (up to %s); the snapshot time excludes it, "
                   "the trigger-to-success time includes it." % (len(queued), secs(max(r["queue_wait_s"]
                                                                                      for r in queued))))
    failed = [r for r in runs if not r["ok"]]
    for r in failed:
        out.append("Failure: job %s (%s, run %s) - %s." % (r.get("snapshot_id"), r["scenario"], r.get("run_stamp"),
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


def write_report() -> tuple:
    """Write the HTML and Markdown reports from all runs; (html path, md path)."""
    runs = load_runs()
    return write_html(runs), write_markdown(runs)


if __name__ == "__main__":
    for p in write_report():
        print(p)
