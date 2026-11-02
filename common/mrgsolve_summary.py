"""mrgsolve run summaries (test_mrgsolve_concurrent and
test_mrgsolve_concurrent_batch): read each session's
mrgsolve_run_summary_PID_<run id>.csv (written by the script in its session
folder) after its run, and report them as one table, one row per session:
evidence/mrgsolve_run_summary_<label>.csv, printed and attached to Allure as
an HTML table and an image.
"""
import csv
import html
import io
import os
import time

try:
    import allure
except ImportError:
    allure = None

from common.perf_report import attach_allure_file, write_csv_report
from common.rstudio_console_commands import read_console_value, read_text_file
from common.step_log import log_step
from config.config import evidence_dir

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


def average_row(rows):
    """An "Average" row for the run summary table: per column, the mean of
    the sessions' values (2 decimals); blank where a column has no numbers
    (Run Id) or no session has a summary."""
    row = ["Average"]
    for i in range(1, len(rows[0]) if rows else 1):
        values = []
        for r in rows:
            try:
                values.append(float(r[i]))
            except (TypeError, ValueError):
                pass
        row.append("%.2f" % (sum(values) / len(values)) if values else "")
    return row


def save_summary_table_png(path, head, rows, title, last_row_total=False):
    """The run summary table drawn as an image at `path`, in the style of the
    session time charts' stats table; with last_row_total the last row (the
    average) is set apart in bold on grey. Returns the path, or None (logged)
    if it could not be drawn."""
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
        elif last_row_total and r == len(rows):
            cell.set_facecolor("#e8ecf2")
            cell.set_text_props(fontweight="bold")
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


def report_run_summaries(runs, label, run_description, run_dir):
    """The run summary table (SUMMARY_COLUMNS, one row per session, in
    session order): written to evidence/mrgsolve_run_summary_<label>.csv,
    printed and attached to Allure as an HTML table and an image (titled with
    `run_description` and `run_dir`). Sessions with no summary get a row of
    blanks; an Average row (average_row(), over the sessions that have one)
    closes the table."""
    head = ["Session Name"] + [header for _, header, _ in SUMMARY_COLUMNS]
    rows = []
    for run in sorted(runs, key=lambda r: int(r["session_name"].rsplit("_", 1)[-1])):
        summary = run.get("mrgsolve_summary") or {}
        rows.append([run["session_name"]] + [_summary_cell(summary, names, decimals) if summary else ""
                                             for names, _, decimals in SUMMARY_COLUMNS])
    rows.append(average_row(rows))
    path = os.path.join(evidence_dir(), "mrgsolve_run_summary_%s.csv" % label)
    write_csv_report(path, [head] + rows)
    widths = [max(len(str(row[i])) for row in [head] + rows) for i in range(len(head))]
    print("\n[rstudio-local] mrgsolve run summary (%s):" % path)
    for row in [head] + rows:
        print("  " + " | ".join(str(cell).ljust(w) for cell, w in zip(row, widths)))
    png = save_summary_table_png(os.path.splitext(path)[0] + ".png", head, rows,
                                 "mrgsolve run summary - %s\nParent folder: %s" % (run_description, run_dir),
                                 last_row_total=True)
    if png:
        attach_allure_file(png, name="mrgsolve run summary (image)")
    if allure is not None:
        table = "".join("<tr%s>%s</tr>" % (" style='font-weight:bold;background:#e8ecf2'" if i == len(rows) - 1 else "",
                                            "".join("<td>%s</td>" % html.escape(str(c)) for c in row))
                        for i, row in enumerate(rows))
        allure.attach(
            "<html><body><table border='1' cellpadding='4' style='border-collapse:collapse;font-family:sans-serif'>"
            "<tr>%s</tr>%s</table></body></html>" % ("".join("<th>%s</th>" % html.escape(h) for h in head), table),
            name="mrgsolve run summary", attachment_type=allure.attachment_type.HTML)
