"""Session time report: bar charts of the time each concurrent session took
to run the script, built from a script timings CSV (see
common/script_timings.py: Session Name, Start Time, End Time,
Time Taken (s), Status).

generate_session_time_report() writes three PNGs next to the CSV and
attaches them to Allure:
  - <name>.png            time per session: minimum (green), maximum
                          (orange), failed (red), average / P95 / threshold
                          lines and a statistics box
  - <name>_histogram.png  how many sessions fall in each time range
  - <name>_timeline.png   each session's start-to-end bar, showing overlap

A session is failed when its time is blank or it has a status other than
ok/pass/success/... (e.g. "timed out", "R error: ...", or "<folder> is an
empty folder" from a finished run whose output check failed). Statistics use successful sessions only. Charts scale to 200+
sessions.

Needs pandas, numpy and matplotlib; without them the report is skipped.
Every error is printed, not raised, so a report never fails a test.

Also runs standalone on an existing file:
    python -m common.session_time_report evidence/rstudio_script_timings_x.csv --threshold 70
"""
import argparse
import os
import re
import statistics as st
from datetime import datetime
from types import SimpleNamespace

from config.perf_report import attach_allure_file

from common.config import env

BAR, MIN_C, MAX_C, FAIL_C, AVG_C = "#9bb4d8", "#2e8b57", "#e67e22", "#c0392b", "#222222"
# Statuses of a successful session (lower case); blank also counts as ok.
OK_STATUSES = ("ok", "pass", "passed", "success", "successful", "completed", "done", "true")
REPORT_TZ = env("SESSION_REPORT_TZ", "Asia/Kolkata")


def _plot_libs():
    """(np, plt, pd, Patch), imported on first use so tests still run
    where the plotting libraries are not installed."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd
    from matplotlib.patches import Patch

    return np, plt, pd, Patch


def _now_stamp(tz):
    """'13 Oct 2026, 14:05 IST (UTC+0530)', in local time if `tz` is unknown
    (e.g. no tzdata on Windows)."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now().astimezone()
    return now.strftime("%d %b %Y, %H:%M %Z (UTC%z)")


def load_session_times(path, sheet=0):
    """DataFrame (session, time, status, start, end, failed) from a .csv /
    .xlsx with at least a session name and time-taken column. Raises
    ValueError when there is no successful session with a numeric time.
    """
    _, _, pd, _ = _plot_libs()
    if path.lower().endswith((".csv", ".txt")):
        df = pd.read_csv(path)
    else:
        df = pd.read_excel(path, sheet_name=sheet)
    if df.shape[1] < 2:
        raise ValueError("%s: need at least 2 columns: session name, time (seconds)" % path)

    def find(*keys):  # first column whose header contains any key
        for c in df.columns:
            if any(k in str(c).lower() for k in keys):
                return c
        return None

    c_sess = find("session", "name") or df.columns[0]
    c_time = find("time taken", "(s)", "seconds", "duration", "elapsed")
    if c_time is None:  # fall back to the first numeric-looking column after the name
        c_time = next((c for c in df.columns[1:]
                       if pd.to_numeric(df[c], errors="coerce").notna().any()), df.columns[1])
    c_stat, c_start, c_end = find("status", "result"), find("start"), find("end")
    blank = pd.Series([""] * len(df))
    df = pd.DataFrame({
        "session": df[c_sess],
        "time": pd.to_numeric(df[c_time], errors="coerce"),
        "status": (df[c_stat] if c_stat is not None else blank).fillna("").astype(str).str.lower(),
        "start": pd.to_datetime(df[c_start], errors="coerce") if c_start is not None else pd.NaT,
        "end": pd.to_datetime(df[c_end], errors="coerce") if c_end is not None else pd.NaT,
    })
    df = df.dropna(subset=["session"]).reset_index(drop=True)
    df["failed"] = df["time"].isna() | df["status"].map(
        lambda s: s.strip() not in OK_STATUSES + ("",))
    if df.empty or (~df["failed"]).sum() == 0:
        raise ValueError("%s: no successful sessions with a numeric time" % path)
    return df


def describe_run(session_count, browser_count, user):
    """'1 session, 2 browsers, user 1' for the chart header."""
    return "%d session%s, %d browser%s, user %d" % (
        session_count, "" if session_count == 1 else "s", browser_count, "" if browser_count == 1 else "s", user)


def short_label(name):
    """CONCURRENT_SESSION_21 -> '21'; falls back to the full name."""
    m = re.search(r"(\d+)\s*$", str(name))
    return m.group(1) if m else str(name)


def _legend_handles(Patch):
    return [Patch(color=MIN_C, label="Minimum time"),
            Patch(color=MAX_C, label="Maximum time"),
            Patch(color=FAIL_C, label="Failed session"),
            Patch(color=BAR, label="Other sessions")]


def build_session_chart(df, out, title, o, sort="session", tz=REPORT_TZ):
    """Chart 1: one bar per session. `o` carries threshold, test_name,
    environment, browser and notes (any may be None/""). Returns the stats."""
    np, plt, pd, Patch = _plot_libs()
    if sort == "session":  # natural order by trailing number, else file order
        key = df["session"].map(lambda s: int(short_label(s)) if short_label(s).isdigit() else 10**9)
        df = df.assign(_k=key).sort_values(["_k"], kind="stable").drop(columns="_k")
    elif sort == "time":
        df = df.sort_values("time", na_position="last")
    df = df.reset_index(drop=True)

    labels = [short_label(s) for s in df["session"]]
    failed = df["failed"].tolist()
    times = df["time"].tolist()
    n = len(df)
    x = list(range(n))

    ok = [t for t, f in zip(times, failed) if not f]
    n_ok, n_fail = len(ok), sum(failed)
    mean = st.mean(ok)
    pvar = st.pvariance(ok)
    svar = st.variance(ok) if n_ok > 1 else 0.0
    sstd = st.stdev(ok) if n_ok > 1 else 0.0
    above = sum(v > mean for v in ok)
    below = sum(v < mean for v in ok)
    vmin, vmax = min(ok), max(ok)
    median = float(np.median(ok))
    p90, p95 = (float(v) for v in np.percentile(ok, [90, 95]))
    success = 100.0 * n_ok / n
    thr = o.threshold
    over_thr = sum(v > thr for v in ok) if thr is not None else None

    def ids(val):
        return ", ".join(labels[i] for i, (t, f) in enumerate(zip(times, failed))
                         if not f and t == val)

    # y-range: tight around successful data, headroom for info boxes
    span = (vmax - vmin) or max(vmax * 0.1, 1)
    ylo = max(0, vmin - span * 0.35)
    yhi = vmax + span * 1.0
    stub = ylo + (vmin - ylo) * 0.6  # height of a failed bar that has no time

    heights, colors = [], []
    for t, f in zip(times, failed):
        if f:
            heights.append(t if pd.notna(t) else stub)
            colors.append(FAIL_C)
        else:
            heights.append(t)
            colors.append(MAX_C if t == vmax else MIN_C if t == vmin else BAR)

    # layout scales with number of sessions (thin bars for 200+)
    width = min(max(9, 0.24 * n + 3), 60)
    fig, ax = plt.subplots(figsize=(width, 7.2), dpi=150)
    ax.bar(x, [h - ylo for h in heights], bottom=ylo, color=colors,
           width=0.8 if n > 40 else 0.75)

    # value labels: vertical, inside the bar, anchored at its top
    lab_fs = 8 if n <= 30 else 7 if n <= 60 else 6 if n <= 120 else 5
    pad = (yhi - ylo) * 0.008
    for xi, h, t, f, c in zip(x, heights, times, failed, colors):
        txt = "FAIL" if (f and pd.isna(t)) else f"{t:.2f}"
        ax.text(xi, h - pad, txt, rotation=90, ha="center", va="top",
                fontsize=lab_fs, color="white" if c != BAR else "#1b2a41",
                fontweight="bold" if c != BAR else "normal")

    ax.axhline(mean, color=AVG_C, ls="--", lw=1.4)
    ax.text(-0.6, mean + pad * 2, f"Average: {mean:.2f} seconds", color=AVG_C,
            fontsize=10, fontweight="bold")

    ax.axhline(p95, color="#7f8c8d", ls=":", lw=1.2)
    ax.text(n - 0.4, p95 + pad * 2, f"P95: {p95:.2f} seconds", color="#555", fontsize=8,
            ha="right")
    if thr is not None:
        ax.axhline(thr, color="#8e44ad", ls="-.", lw=1.4)
        ax.text(-0.6, thr + pad * 2, f"Threshold: {thr:g} seconds", color="#8e44ad",
                fontsize=9, fontweight="bold")
        yhi = max(yhi, thr + span * 0.2)

    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-0.8, n - 0.2)
    ax.set_xticks(x)
    tick_fs = 9 if n <= 30 else 7 if n <= 60 else 6 if n <= 120 else 5
    ax.set_xticklabels(labels, rotation=0 if n <= 30 else 90, fontsize=tick_fs)
    ax.set_xlabel("Concurrent session (Sessions opened in separate browser tab)")
    ax.set_ylabel("Time taken to execute script (Seconds)")
    # header: title + test details + test run time stamps (when available)
    sub = []
    det = [f"{k}: {v}" for k, v in (("Test", o.test_name), ("Environment", o.environment),
                                    ("Browser", o.browser), ("Notes", o.notes)) if v]
    if det:
        sub.append("   |   ".join(det))
    if df["start"].notna().any():  # time stamps from the data, shown as recorded
        t0 = df["start"].min()
        t1 = df["end"].max() if df["end"].notna().any() else df["start"].max()
        fmt = "%d %b %Y, %H:%M:%S"
        sub.append(f"Test run: {t0.strftime(fmt)} to {t1.strftime(fmt)} "
                   f"(total {(t1 - t0).total_seconds():.1f} seconds, timestamps as recorded in the data)")
    ax.set_title(title, loc="left", fontweight="bold", pad=8 + 14 * len(sub))
    for i, line in enumerate(sub):
        ax.annotate(line, (0, 1), xycoords="axes fraction", textcoords="offset points",
                    xytext=(0, 4 + 14 * (len(sub) - 1 - i)), va="bottom", fontsize=9, color="#555")

    stats = (f"Average time: {mean:.2f} seconds\n"
             f"Median time: {median:.2f} seconds\n"
             f"P90: {p90:.2f} seconds\n"
             f"P95: {p95:.2f} seconds\n"
             f"Variance (sample): {svar:.2f} seconds²\n"
             f"Variance (population): {pvar:.2f} seconds²\n"
             f"Std dev (sample): {sstd:.2f} seconds\n"
             f"Sessions above average: {above}\n"
             f"Sessions below average: {below}\n"
             f"Success rate: {n_ok} of {n} ({success:.1f}%)\n"
             f"Failed sessions: {n_fail}"
             + (f"\nSessions above threshold ({thr:g} s): {over_thr}" if thr is not None else ""))
    ax.text(0.99, 0.97, stats, transform=ax.transAxes, ha="right", va="top",
            fontsize=9, bbox=dict(fc="white", ec="#ccc"))

    # Max / Min / Failed boxes sit just left of the stats box
    box_right = 1 - 3.0 / width - 0.02
    ax.text(box_right, 0.97, f"Max: {vmax} seconds (Session {ids(vmax)})",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            color=MAX_C, fontweight="bold", bbox=dict(fc="white", ec=MAX_C))
    ax.text(box_right, 0.90, f"Min: {vmin} seconds (Session {ids(vmin)})",
            transform=ax.transAxes, ha="right", va="top", fontsize=9,
            color=MIN_C, fontweight="bold", bbox=dict(fc="white", ec=MIN_C))
    if n_fail:
        fl = ", ".join(l for l, f in zip(labels, failed) if f)
        if len(fl) > 60:
            fl = fl[:57] + "..."
        ax.text(box_right, 0.83, f"Failed: {n_fail} (Session {fl})",
                transform=ax.transAxes, ha="right", va="top", fontsize=9,
                color=FAIL_C, fontweight="bold", bbox=dict(fc="white", ec=FAIL_C))

    # One column on narrow charts, so the legend clears the Max / Min boxes.
    ax.legend(handles=_legend_handles(Patch), loc="upper left", fontsize=8, frameon=False,
              ncol=2 if width >= 14 else 1)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="y", alpha=0.3)

    fig.text(0.01, 0.01, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out)
    plt.close(fig)

    return dict(sessions=n, successful=n_ok, failed=n_fail, success_rate_pct=success,
                mean=mean, median=median, p90=p90, p95=p95, min=vmin, max=vmax, var_sample=svar,
                var_pop=pvar, std=sstd, above=above, below=below,
                **({"above_threshold": over_thr} if thr is not None else {}))


def build_timeline_chart(df, out, title, tz=REPORT_TZ):
    """Chart 2: one horizontal bar per session, from its start to its end
    time, so the overlap (concurrency) between sessions is visible. Returns
    None (no chart) when the data has no start times."""
    _, plt, pd, Patch = _plot_libs()
    d = df[df["start"].notna()].copy()
    if d.empty:
        return None
    # end time: recorded end, else start + time taken, else start (zero length)
    d["end2"] = d["end"].fillna(d["start"] + pd.to_timedelta(d["time"].fillna(0), unit="s"))
    d = d.sort_values("start", kind="stable").reset_index(drop=True)
    n = len(d)
    t0 = d["start"].min()
    off = (d["start"] - t0).dt.total_seconds().tolist()
    dur = (d["end2"] - d["start"]).dt.total_seconds().tolist()
    labels = [short_label(s) for s in d["session"]]
    failed = d["failed"].tolist()
    times = d["time"].tolist()

    ok = [t for t, f in zip(times, failed) if not f]
    vmin, vmax = (min(ok), max(ok)) if ok else (None, None)
    colors = [FAIL_C if f else MAX_C if t == vmax else MIN_C if t == vmin else BAR
              for t, f in zip(times, failed)]

    # peak number of sessions running at the same moment
    events = sorted([(o, 1) for o in off] + [(o + du, -1) for o, du in zip(off, dur)],
                    key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, k in events:
        cur += k
        peak = max(peak, cur)
    start_spread = max(off)
    total = max(o + du for o, du in zip(off, dur)) or 1.0

    height = min(max(4.5, 0.27 * n + 2.2), 60)
    fig, ax = plt.subplots(figsize=(12, height), dpi=150)
    y = list(range(n))
    ax.barh(y, dur, left=off, color=colors, height=0.72)
    fs = 8 if n <= 30 else 7 if n <= 60 else 5
    for yi, o, du, t, f, c in zip(y, off, dur, times, failed, colors):
        txt = "FAIL" if (f and pd.isna(t)) else f"{(t if pd.notna(t) else du):.2f} seconds"
        ax.text(o + du / 2, yi, txt, ha="center", va="center", fontsize=fs,
                color="white" if c != BAR else "#1b2a41",
                fontweight="bold" if c != BAR else "normal")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9 if n <= 30 else 7 if n <= 60 else 5)
    ax.invert_yaxis()
    ax.set_ylim(n - 0.4, -0.9)
    ax.set_xlim(-total * 0.01, total * 1.02)
    ax.set_xlabel(f"Seconds since first session started "
                  f"({t0.strftime('%d %b %Y, %H:%M:%S')}, timestamps as recorded in the data)")
    ax.set_ylabel("Concurrent session (Sessions opened in separate browser tab)")
    ax.grid(axis="x", alpha=0.3)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    info = (f"Sessions: {n}   |   Peak running at the same time: {peak}   |   "
            f"Start spread (first to last start): {start_spread:.2f} seconds   |   "
            f"Total test duration: {total:.2f} seconds")
    ax.set_title(f"{title}: start to end timeline", loc="left", fontweight="bold", pad=22)
    ax.text(0, 1.008, info, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=9, color="#555")
    ax.legend(handles=_legend_handles(Patch), loc="upper left",
              bbox_to_anchor=(0, -0.07 if n <= 60 else -0.05), fontsize=8, frameon=False, ncol=4)
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(out)
    plt.close(fig)
    return dict(peak_concurrent=peak, start_spread=start_spread, total_duration=total)


def build_histogram_chart(df, out, title, o, tz=REPORT_TZ):
    """Chart 3: distribution of time taken (sessions per time range)."""
    np, plt, _, _ = _plot_libs()
    ok = df.loc[~df["failed"], "time"].astype(float).to_numpy()
    nb = int(min(max(np.ceil(np.sqrt(len(ok))), 5), 30))
    counts, edges = np.histogram(ok, bins=nb)
    mids, w = (edges[:-1] + edges[1:]) / 2, edges[1] - edges[0]
    vmin, vmax = ok.min(), ok.max()
    cols = [MIN_C if e0 <= vmin < e1 + (1e-9 if i == nb - 1 else 0) else
            MAX_C if e0 <= vmax <= e1 else BAR
            for i, (e0, e1) in enumerate(zip(edges[:-1], edges[1:]))]
    fig, ax = plt.subplots(figsize=(11, 5.8), dpi=150)
    ax.bar(mids, counts, width=w * 0.92, color=cols)
    for m_, c_ in zip(mids, counts):
        if c_:
            ax.text(m_, c_ + max(counts) * 0.01, str(c_), ha="center", va="bottom", fontsize=9)
    mean, median = ok.mean(), float(np.median(ok))
    p95 = np.percentile(ok, 95)
    for val, lab, col, ls in ((mean, f"Average {mean:.2f} seconds", AVG_C, "--"),
                              (median, f"Median {median:.2f} seconds", "#2c6fbb", "-"),
                              (p95, f"P95 {p95:.2f} seconds", "#7f8c8d", ":")):
        ax.axvline(val, ymax=0.68, color=col, ls=ls, lw=1.5, label=lab)
    if o.threshold is not None:
        ax.axvline(o.threshold, ymax=0.68, color="#8e44ad", ls="-.", lw=1.5,
                   label=f"Threshold {o.threshold:g} seconds")
    ax.set_ylim(0, max(counts) * 1.75)
    ax.set_xlabel("Time taken to execute script (Seconds)")
    ax.set_ylabel("Number of sessions")
    ax.set_title(f"{title}: distribution of time taken", loc="left", fontweight="bold")
    ax.legend(loc="upper right", fontsize=9, frameon=False)
    ax.text(0.01, 0.97, f"Sessions: {len(df)}  (successful: {len(ok)}, failed: {int(df['failed'].sum())})\n"
            f"Minimum {vmin:.2f} seconds (green)   Maximum {vmax:.2f} seconds (orange)\n"
            f"Each bar = sessions whose time falls in a {w:.2f}-second range",
            transform=ax.transAxes, va="top", fontsize=9, color="#555")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def generate_session_time_report(timings_path, title="Time taken per concurrent session", threshold=None,
                                 test_name="", environment="", browser="", notes="", sort="session",
                                 tz=REPORT_TZ, attach=True):
    """Build the session, histogram and timeline charts from `timings_path`
    as <timings_path without extension>.png / _histogram.png / _timeline.png
    and attach them to Allure (when `attach`). Returns the stats dict (plus
    "charts": the PNG paths), or None if the report could not be made.
    Never raises.
    """
    if not timings_path or not os.path.exists(timings_path):
        print("\n[rstudio-local] no script timings at %s - session time report skipped" % timings_path)
        return None
    try:
        _plot_libs()
    except ImportError as exc:
        print("\n[rstudio-local] session time report skipped (%s); pip install pandas numpy matplotlib" % exc)
        return None
    o = SimpleNamespace(threshold=threshold, test_name=test_name, environment=environment,
                        browser=browser, notes=notes)
    base = os.path.splitext(timings_path)[0]
    charts = []
    result = None
    try:
        df = load_session_times(timings_path)
        result = build_session_chart(df, base + ".png", title, o, sort=sort, tz=tz)
        charts.append(base + ".png")
        build_histogram_chart(df, base + "_histogram.png", title, o, tz=tz)
        charts.append(base + "_histogram.png")
        timeline = build_timeline_chart(df, base + "_timeline.png", title, tz=tz)
        if timeline:
            result.update(timeline)
            charts.append(base + "_timeline.png")
    except Exception as exc:
        print("\n[rstudio-local] could not build the session time report from %s: %s" % (timings_path, exc))
    for path in charts:
        print("[rstudio-local] wrote session time chart %s" % path)
        if attach:
            attach_allure_file(path)
    if result is None:
        return None
    result["charts"] = charts
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", help=".csv / .xlsx (session, time in seconds, [status, start, end])")
    p.add_argument("--title", default="Time taken per concurrent session")
    p.add_argument("--sort", choices=["session", "time", "none"], default="session",
                   help="bar order (default: by session number)")
    p.add_argument("--tz", default=REPORT_TZ, help="timezone for the 'Generated' stamp")
    p.add_argument("--threshold", type=float, default=None,
                   help="slow-session limit in seconds; draws a line and counts sessions above it")
    p.add_argument("--test-name", default="", help="shown in the chart header")
    p.add_argument("--environment", default="", help="e.g. staging / prod, shown in the header")
    p.add_argument("--browser", default="", help="e.g. Chrome 130, shown in the header")
    p.add_argument("--notes", default="", help="free text, shown in the header")
    a = p.parse_args()
    r = generate_session_time_report(a.input, a.title, a.threshold, a.test_name, a.environment, a.browser,
                                     a.notes, a.sort, a.tz, attach=False)
    if r is None:
        raise SystemExit(1)
    for k, v in r.items():
        print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
