"""Result lines printed per session, and the one-line summaries of a run."""
from common.script_timings import failed_runs
import time
import os
from config.config import EVIDENCE_DIR
from common.perf_report import write_json_report
from common.perf_report import attach_allure_file


def format_timings(timings):
    """Comma-separated "%.2f" list of timings, for the end-of-test summary."""
    return ", ".join("%.2f" % t for t in timings)


def failed(results):
    """Every result dict whose "ok" is falsy."""
    return [r for r in results if not r["ok"]]


def print_results(results, total, kind, describe):
    """One line per result: "<kind> i/total session=<name>: <describe(r)>" on
    success, "<kind> i/total (<name>) failed: <error>" otherwise."""
    for r in results:
        if r["ok"]:
            print("\n[rstudio-local] %s %d/%d session=%s: %s"
                  % (kind, r["index"], total, r["session_name"], describe(r)))
        else:
            print("\n[rstudio-local] %s %d/%d (%s) failed: %s"
                  % (kind, r["index"], total, r.get("session_name"), r["error"]))


def describe_script_runs(command):
    """Result describer for script runs: launch time plus each run's time."""
    return lambda r: "launch=%.2fs, %d run(s) of %r: %s" % (
        r["launch_elapsed_s"], len(r["run_elapsed_s"]), command,
        ["%.2fs" % e for e in r["run_elapsed_s"]],
    )


def describe_launch_project_text_file(r):
    """Result describer: launch, project and text-file times."""
    return "launch=%.2fs project=%.2fs text_file=%.2fs" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], r["text_file_elapsed_s"])


def describe_launch_project_text_files(r):
    """Result describer: launch and project times plus each text file's."""
    return "launch=%.2fs project=%.2fs text_files=%s" % (
        r["launch_elapsed_s"], r["project_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def describe_launch_project(r):
    """Result describer: launch and project times."""
    return "launch=%.2fs project=%.2fs" % (r["launch_elapsed_s"], r["project_elapsed_s"])


def describe_open_text_files(r):
    """Result describer: open time plus each text file's."""
    return "open=%.2fs text_files=%s" % (
        r["open_elapsed_s"], ["%.2fs" % s for s in r["text_file_elapsed_s"]])


def print_script_result(label, result, command):
    """Print one session's script-run result under `label`."""
    print("\n[rstudio-local] %s session=%s: %s"
          % (label, result["session_name"], describe_script_runs(command)(result)))


# --- Run summaries -------------------------------------------------------------


def report_failed_runs(runs, label, started, idle_sessions=()):
    """Print a one-line summary of `runs` (and of the sessions left idle) and
    return failed_runs(runs)."""
    failures = failed_runs(runs)
    print(
        "\n[rstudio-local] %s: %d of %d run(s) finished successfully, %d session(s) left idle (%s), %.2fs in total"
        % (label, len(runs) - len(failures), len(runs), len(idle_sessions),
           ", ".join(s.session_name for s in idle_sessions), time.time() - started)
    )
    return failures


def print_script_runs_summary(label, runs, script_path, started):
    """One line: how many of `runs` succeeded and the time since `started`."""
    failures = failed_runs(runs)
    print(
        "\n[rstudio-local] %s: %d of %d session(s) ran %s successfully, %.2fs in total"
        % (label, len(runs) - len(failures), len(runs), script_path, time.time() - started)
    )


def summarize_concurrent_runs(runs, label, session_count, browser_count):
    """Print and save the time the script took across all sessions: from
    the first start to the last finish (wall clock), plus the fastest,
    average and slowest run among those that finished ok. Written to
    evidence/rstudio_concurrent_summary_<label>.json and attached to Allure.
    Returns the summary dict.
    """
    ok = [r for r in runs if r["status"] == "ok" and r["started"] is not None and r["ended"] is not None]
    durations = sorted(r["ended"] - r["started"] for r in ok)
    started = [r["started"] for r in runs if r["started"] is not None]
    ended = [r["ended"] for r in ok]
    summary = {
        "label": label,
        "sessions": session_count,
        "browsers": browser_count,
        "runs": len(runs),
        "ok": len(ok),
        "failed": len(runs) - len(ok),
        "wall_clock_s": round(max(ended) - min(started), 2) if ended and started else None,
        "min_s": round(durations[0], 2) if durations else None,
        "avg_s": round(sum(durations) / len(durations), 2) if durations else None,
        "max_s": round(durations[-1], 2) if durations else None,
    }
    print(
        "\n[rstudio-local] %s: %d of %d session(s) ran the script ok across %d browser(s); "
        "all sessions took %ss (first start to last finish), per session min %ss / avg %ss / max %ss"
        % (label, summary["ok"], summary["runs"], browser_count, summary["wall_clock_s"],
           summary["min_s"], summary["avg_s"], summary["max_s"])
    )
    path = os.path.join(EVIDENCE_DIR, "rstudio_concurrent_summary_%s.json" % label)
    write_json_report(path, summary)
    attach_allure_file(path)
    return summary
