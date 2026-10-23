"""Charts of the time each session took, from a script timings CSV
(Session Name, Start Time, End Time, Time Taken (s), Status), written next
to the CSV and attached to Allure:
  <name>.png            time per session (min / max / failed, average, P95,
                        threshold, stats box)
  <name>_histogram.png  sessions per time range
  <name>_timeline.png   start-to-end bar per session
  <name>_replicates.png replicates each session completed - only when the
                        CSV has a Replicates column (scripts with a run summary)

When the CSV's Script column names more than one R script, the sessions are
grouped by script: one color and labelled band per script, each script's
own average / fastest / slowest, and a per-script stats table instead of
the overall stats box (times of different scripts are not comparable).

Also a command line tool: python -m common.session_time_report <csv> ...
"""
import argparse
import os
import re
import statistics as st
from datetime import datetime
from types import SimpleNamespace

from common.perf_report import attach_allure_file

from config.config import current_test_file, env

BAR, MIN_C, MAX_C, FAIL_C, AVG_C = "#9bb4d8", "#2e8b57", "#e67e22", "#c0392b", "#222222"
INK = "#1b2a41"
# One color per R script when the sessions ran several, in this fixed order
# (red is left out: it marks failed sessions).
SCRIPT_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
# Scripts get a time axis each when their averages differ more than this.
SEPARATE_PANELS_RATIO = 3
# Statuses of a successful session (lower case); blank also counts as ok.
OK_STATUSES = ("ok", "pass", "passed", "success", "successful", "completed", "done", "true")
REPORT_TZ = env("SESSION_REPORT_TZ", "Asia/Kolkata")


def _running_test_file():
    """test_concurrent_sessions.py while that test runs, "" outside pytest."""
    name = current_test_file()
    return name + ".py" if name else ""


def script_line(test_file, script):
    """'Script name: <test file>   |   Path: <R script path>' (either part
    left out when blank)."""
    parts = [f"Script name: {test_file}" if test_file else "", f"Path: {script}" if script else ""]
    return "   |   ".join(p for p in parts if p)


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
    # exact header: "Script Runtime (s)" must not match
    c_script = next((c for c in df.columns if str(c).strip().lower() == "script"), None)
    blank = pd.Series([""] * len(df))
    df = pd.DataFrame({
        "script": (df[c_script] if c_script is not None else blank).fillna("").astype(str).str.strip(),
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


def _session_number(name):
    """Trailing number of a session name for sorting, 10**9 without one."""
    label = short_label(name)
    return int(label) if label.isdigit() else 10**9


def script_groups(df):
    """The scripts of df["script"] by name (unknown last), when the sessions
    ran more than one script, else [] (no grouping)."""
    scripts = sorted(set(df["script"]), key=lambda s: (not s, s.lower()))
    return scripts if len([s for s in scripts if s]) > 1 else []


def script_title(script):
    return script or "(unknown script)"


def _bar_labels(sessions, scripts):
    """Bar labels for grouped charts: the trailing number (short_label())
    while that is unique within each script; otherwise the session names
    without the prefix all of them share (e.g. a node prefix "N1_"), with
    "#2", "#3", ... on repeated runs of one session (e.g. list.files() calls)."""
    sessions, scripts = [str(s) for s in sessions], list(scripts)
    short = [short_label(s) for s in sessions]
    if all(len({l for l, g in zip(short, scripts) if g == grp}) == scripts.count(grp) for grp in set(scripts)):
        return short
    common = os.path.commonprefix(sessions)
    common = common[:common.rfind("_") + 1]
    seen, labels = {}, []
    for s in sessions:
        seen[s] = seen.get(s, 0) + 1
        labels.append(s[len(common):] + (" #%d" % seen[s] if seen[s] > 1 else ""))
    return labels


def _group_sorted(df, groups):
    """`df` with each script's sessions together (scripts in `groups` order),
    by session number within a script."""
    rank = {s: i for i, s in enumerate(groups)}
    return (df.assign(_g=df["script"].map(lambda s: rank.get(s, len(groups))),
                      _k=df["session"].map(_session_number))
            .sort_values(["_g", "_k", "session"], kind="stable").drop(columns=["_g", "_k"]).reset_index(drop=True))


def _needs_separate_axes(df, groups):
    """True when the scripts' average times differ more than
    SEPARATE_PANELS_RATIO times, too much to share one time axis."""
    good = df[~df["failed"]]
    means = [m for m in (good.loc[good["script"] == g, "time"].mean() for g in groups) if m == m]
    return len(means) > 1 and max(means) > SEPARATE_PANELS_RATIO * max(min(means), 1e-9)


def _script_color(groups, script):
    return SCRIPT_COLORS[groups.index(script) % len(SCRIPT_COLORS)] if script in groups else BAR


def _ink_on(color):
    """Text color readable on a fill of `color`."""
    from matplotlib.colors import to_rgb

    r, g, b = to_rgb(color)
    return "white" if 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.55 else INK


def _tint(color, amount):
    """`color` mixed with white (amount 0 = white, 1 = the color)."""
    from matplotlib.colors import to_hex, to_rgb

    return to_hex([1 - amount * (1 - c) for c in to_rgb(color)])


def _script_handles(Patch, groups, with_failed=True):
    return ([Patch(color=_script_color(groups, g), label=script_title(g)) for g in groups]
            + ([Patch(facecolor=FAIL_C, hatch="///", edgecolor="white", label="Failed session")]
               if with_failed else []))


def _legend_handles(Patch):
    return [Patch(color=MIN_C, label="Minimum time"),
            Patch(color=MAX_C, label="Maximum time"),
            Patch(color=FAIL_C, label="Failed session"),
            Patch(color=BAR, label="Other sessions")]


def _draw_header(ax, df, title, o, extra=()):
    """Title + _header_lines() above `ax`."""
    sub = _header_lines(df, o, extra)
    ax.set_title(title, loc="left", fontweight="bold", pad=8 + 14 * len(sub))
    for i, line in enumerate(sub):
        ax.annotate(line, (0, 1), xycoords="axes fraction", textcoords="offset points",
                    xytext=(0, 4 + 14 * (len(sub) - 1 - i)), va="bottom", fontsize=9, color="#555")


def _header_lines(df, o, extra=()):
    """Test details + script + test run time stamps (when available) +
    `extra` lines, for under a chart's title."""
    sub = []
    det = [f"{k}: {v}" for k, v in (("Test", o.test_name), ("Environment", o.environment),
                                    ("Browser", o.browser), ("Notes", o.notes)) if v]
    if det:
        sub.append("   |   ".join(det))
    script_info = script_line(getattr(o, "test_file", ""), getattr(o, "script", ""))
    if script_info:
        sub.append(script_info)
    if df["start"].notna().any():  # time stamps from the data, shown as recorded
        t0 = df["start"].min()
        t1 = df["end"].max() if df["end"].notna().any() else df["start"].max()
        fmt = "%d %b %Y, %H:%M:%S"
        sub.append(f"Test run: {t0.strftime(fmt)} to {t1.strftime(fmt)} "
                   f"(total {(t1 - t0).total_seconds():.1f} seconds, timestamps as recorded in the data)")
    sub.extend(extra)
    return sub


def _time_stats(times):
    """Stats of a list of (successful) times."""
    import numpy as np

    p90, p95 = (float(v) for v in np.percentile(times, [90, 95]))
    mean = st.mean(times)
    return dict(mean=mean, median=float(np.median(times)), p90=p90, p95=p95, min=min(times), max=max(times),
                var_sample=st.variance(times) if len(times) > 1 else 0.0, var_pop=st.pvariance(times),
                std=st.stdev(times) if len(times) > 1 else 0.0,
                above=sum(v > mean for v in times), below=sum(v < mean for v in times))


def build_grouped_session_chart(df, out, title, o, groups, tz=REPORT_TZ):
    """Chart 1 for sessions running different scripts: the sessions of each
    script side by side under a band with the script's name, in the
    script's color, with the script's own average line and its fastest (▼)
    and slowest (▲) session marked; a table of per-script stats below.
    Returns the overall stats plus "per_script": {script: stats}."""
    np, plt, pd, Patch = _plot_libs()
    from matplotlib.lines import Line2D
    from matplotlib.transforms import blended_transform_factory

    df = _group_sorted(df, groups)
    labels = _bar_labels(df["session"], df["script"])
    times, failed, scripts = df["time"].tolist(), df["failed"].tolist(), df["script"].tolist()
    n = len(df)

    ok = [t for t, f in zip(times, failed) if not f]
    n_ok, n_fail = len(ok), sum(failed)
    overall = _time_stats(ok)
    per = {}
    for g in groups:
        idx = [i for i, s in enumerate(scripts) if s == g]
        g_ok = [times[i] for i in idx if not failed[i]]
        per[g] = dict(idx=idx, sessions=len(idx), failed=sum(failed[i] for i in idx),
                      **(_time_stats(g_ok) if g_ok else {}))

    vmin, vmax = overall["min"], overall["max"]
    thr = o.threshold
    # Scripts whose times are of the same order share one time axis, so they
    # can be compared; otherwise (e.g. 0.3 s list.files() calls next to a
    # 180 s background job) each gets a panel with a time axis of its own.
    separate = _needs_separate_axes(df, groups)
    panels = [[g] for g in groups] if separate else [groups]
    # x position of each bar within its panel, one empty slot between scripts
    x, panel_slots = [0] * n, []
    for panel in panels:
        pos = -1
        for k, g in enumerate(panel):
            for j, i in enumerate(per[g]["idx"]):
                pos += 2 if k and not j else 1
                x[i] = pos
        panel_slots.append(pos + 1)

    # wide enough for every script's name over its bars
    per_slot = max([0.24] + [(len(script_title(g)) * 0.085 + 0.3) / (len(per[g]["idx"]) + 1) for g in groups])
    width = min(max(10, per_slot * sum(panel_slots) + 3 + 0.9 * (len(panels) - 1)), 60)
    rows = len(groups) + 2  # header + scripts + all sessions
    table_h = 0.3 * rows + 0.5
    head_lines = _header_lines(df, o, extra=[
        f"{len(groups)} scripts - sessions grouped by script; compare times within a script"
        + ("; each script has its own time axis (their times differ more than %gx)" % SEPARATE_PANELS_RATIO
           if separate else "")
        + f"   |   Success rate: {n_ok} of {n} ({100.0 * n_ok / n:.1f}%)   |   Failed sessions: {n_fail}"])
    head_h = 0.35 + 0.2 * len(head_lines) + 0.35  # title, lines, legend (inches)
    fig_h = head_h + 6.6 + table_h
    fig = plt.figure(figsize=(width, fig_h), dpi=150)
    outer = fig.add_gridspec(2, 1, height_ratios=[6.6, table_h])
    top_row = outer[0].subgridspec(1, len(panels), width_ratios=panel_slots)
    axes = [fig.add_subplot(top_row[0, k]) for k in range(len(panels))]
    tax = fig.add_subplot(outer[1])

    lab_fs = 8 if n <= 30 else 7 if n <= 60 else 6 if n <= 120 else 5
    colors = [FAIL_C if f else _script_color(groups, s) for f, s in zip(failed, scripts)]
    for ax, panel, slots in zip(axes, panels, panel_slots):
        idx = [i for g in panel for i in per[g]["idx"]]
        p_ok = [times[i] for i in idx if not failed[i]] or [vmin]
        p_min, p_max = min(p_ok), max(p_ok)
        span = (p_max - p_min) or max(p_max * 0.1, 1e-3)
        ylo = max(0, p_min - span * 0.35)
        yhi = p_max + span * 0.75  # headroom for the script bands and markers
        # the threshold only where it is near this panel's times
        show_thr = thr is not None and (not separate or thr <= yhi + span)
        if show_thr:
            yhi = max(yhi, thr + span * 0.45)
        stub = ylo + (p_min - ylo) * 0.6  # height of a failed bar that has no time
        heights = {i: times[i] if not failed[i] or pd.notna(times[i]) else stub for i in idx}

        bars = ax.bar([x[i] for i in idx], [heights[i] - ylo for i in idx], bottom=ylo,
                      color=[colors[i] for i in idx], width=0.8, edgecolor="white", linewidth=0.6)
        for b, i in zip(bars, idx):
            if failed[i]:
                b.set_hatch("///")

        pad = (yhi - ylo) * 0.008
        # a bar too short for its value gets it above the bar, and its
        # ▲ / ▽ marker above that
        label_h = (yhi - ylo) * 0.09
        above = {i: heights[i] - ylo < label_h for i in idx}
        for i in idx:
            txt = "FAIL" if (failed[i] and pd.isna(times[i])) else f"{times[i]:.2f}"
            ax.text(x[i], heights[i] + pad if above[i] else heights[i] - pad, txt, rotation=90, ha="center",
                    va="bottom" if above[i] else "top", fontsize=lab_fs,
                    color=INK if above[i] else _ink_on(colors[i]))

        band = blended_transform_factory(ax.transData, ax.transAxes)
        for g in panel:
            p = per[g]
            x0, x1 = x[p["idx"][0]], x[p["idx"][-1]]
            color = _script_color(groups, g)
            ax.axvspan(x0 - 0.5, x1 + 0.5, color=_tint(color, 0.08), lw=0, zorder=0)
            ax.text((x0 + x1) / 2, 0.985, script_title(g), transform=band, ha="center", va="top", fontsize=9,
                    fontweight="bold", color=INK,
                    bbox=dict(boxstyle="round,pad=0.35", fc=_tint(color, 0.25), ec=color, lw=1.2))
            ax.text((x0 + x1) / 2, 0.93, f"avg {p['mean']:.2f} s" if "mean" in p else "all failed",
                    transform=band, ha="center", va="top", fontsize=8, color=INK)
            if "mean" in p:
                ax.hlines(p["mean"], x0 - 0.45, x1 + 0.45, colors=AVG_C, linestyles="--", lw=1.3, zorder=3)
            g_ok = [i for i in p["idx"] if not failed[i]]
            if len(g_ok) > 1 and p["max"] != p["min"]:
                for i in g_ok:
                    if times[i] in (p["max"], p["min"]):
                        slow = times[i] == p["max"]
                        ax.plot(x[i], heights[i] + pad * 3 + (label_h if above[i] else 0),
                                marker="^" if slow else "v", ms=7, ls="none",
                                color=INK if slow else "white", mec=INK, mew=1.2, zorder=5)

        if show_thr:
            ax.axhline(thr, color="#8e44ad", ls="-.", lw=1.4)
            ax.text(slots - 0.4, thr + pad * 2, f"Threshold: {thr:g} seconds", color="#8e44ad",
                    fontsize=9, fontweight="bold", ha="right")

        p_labels = [labels[i] for i in idx]
        ax.set_ylim(ylo, yhi)
        ax.set_xlim(-0.8, slots - 0.2)
        ax.set_xticks([x[i] for i in idx])
        ax.set_xticklabels(p_labels, rotation=0 if len(idx) <= 30 and max(map(len, p_labels)) <= 3 else 90,
                           fontsize=9 if n <= 30 else 7 if n <= 60 else 5)
        ax.set_xlabel("Concurrent session" if separate else
                      "Concurrent session (Sessions opened in separate browser tab), grouped by R script")
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.grid(axis="y", alpha=0.3)
    axes[0].set_ylabel("Time taken to execute script (Seconds)")

    # header and legend across the top of the figure
    top = 1 - 0.2 / fig_h
    fig.text(0.01, top, title, fontsize=12, fontweight="bold", va="top")
    for k, line in enumerate(head_lines):
        fig.text(0.01, top - (0.3 + 0.2 * k) / fig_h, line, fontsize=9, color="#555", va="top")
    marks = [Line2D([], [], color=AVG_C, ls="--", lw=1.3, label="Script average"),
             Line2D([], [], marker="^", ls="none", color=INK, mec=INK, ms=7, label="Slowest of its script"),
             Line2D([], [], marker="v", ls="none", color="white", mec=INK, mew=1.2, ms=7,
                    label="Fastest of its script")]
    if n_fail:
        marks.append(Patch(facecolor=FAIL_C, hatch="///", edgecolor="white", label="Failed session"))
    fig.legend(handles=marks, loc="upper left", bbox_to_anchor=(0.005, top - (0.3 + 0.2 * len(head_lines)) / fig_h),
               fontsize=8, frameon=False, ncol=len(marks))

    # per-script stats table
    def at(p, key):  # "598.05 (#5)": the time and the sessions with it
        who = ", ".join(("#" if labels[i].isdigit() else "") + labels[i]
                        for i in p["idx"] if not failed[i] and times[i] == p[key])
        return f"{p[key]:.2f} ({who})"

    # comparing scripts' averages only makes sense on a shared time axis
    fastest = None if separate else min((per[g]["mean"] for g in groups if "mean" in per[g]), default=None)
    head = ["Script", "Sessions", "Failed", "Average (s)", "Median (s)", "Min (s)", "Max (s)",
            "Std dev (s)", "P95 (s)", "Max - min (s)"] + (["Average vs fastest script"] if fastest else [])
    cells, fills = [], []
    for g in groups:
        p = per[g]
        if "mean" in p:
            cells.append([script_title(g), p["sessions"], p["failed"], f"{p['mean']:.2f}", f"{p['median']:.2f}",
                          at(p, "min"), at(p, "max"), f"{p['std']:.2f}", f"{p['p95']:.2f}",
                          f"{p['max'] - p['min']:.2f}"]
                         + (["fastest" if p["mean"] == fastest else f"+{100.0 * (p['mean'] / fastest - 1):.1f}%"]
                            if fastest else []))
        else:
            cells.append([script_title(g), p["sessions"], p["failed"], "all failed"] + ["-"] * (len(head) - 4))
        fills.append(_tint(_script_color(groups, g), 0.22))
    cells.append(["All sessions (mixed scripts)", n, n_fail, f"{overall['mean']:.2f}", f"{overall['median']:.2f}",
                  f"{vmin:.2f}", f"{vmax:.2f}", f"{overall['std']:.2f}", f"{overall['p95']:.2f}",
                  f"{vmax - vmin:.2f}"] + ([""] if fastest else []))
    fills.append("#f2f2f2")
    tax.axis("off")
    table = tax.table(cellText=cells, colLabels=head, loc="upper center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8.5)
    table.scale(1, 1.35)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#d0d0d0")
        if r == 0:
            cell.set_facecolor("#e8ecf2")
            cell.set_text_props(fontweight="bold", color=INK)
        elif c == 0:
            cell.set_facecolor(fills[r - 1])
            cell.set_text_props(fontweight="bold", color=INK, ha="left")
    table.auto_set_column_width(list(range(len(head))))

    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.02, 1, 1 - head_h / fig_h))
    fig.savefig(out)
    plt.close(fig)

    result = dict(sessions=n, successful=n_ok, failed=n_fail, success_rate_pct=100.0 * n_ok / n,
                  **overall, **({"above_threshold": sum(v > thr for v in ok)} if thr is not None else {}))
    result["per_script"] = {script_title(g): {k: v for k, v in per[g].items() if k != "idx"} for g in groups}
    return result


def build_session_chart(df, out, title, o, sort="session", tz=REPORT_TZ):
    """Chart 1: one bar per session. `o` carries threshold, test_name,
    environment, browser and notes (any may be None/""). Returns the stats.
    Sessions running different scripts: build_grouped_session_chart()."""
    groups = script_groups(df)
    if groups:
        return build_grouped_session_chart(df, out, title, o, groups, tz=tz)
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
    _draw_header(ax, df, title, o)

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


def build_timeline_chart(df, out, title, tz=REPORT_TZ, script="", test_file=""):
    """Chart 2: one horizontal bar per session, from its start to its end
    time, so the overlap (concurrency) between sessions is visible. Every
    session gets a row: a failed one is drawn in red with "FAIL - <status>"
    next to it, and one that never started (e.g. its setwd failed) is listed
    at the bottom. Returns None (no chart) when no session has a start time."""
    _, plt, pd, Patch = _plot_libs()
    started = df[df["start"].notna()].copy()
    if started.empty:
        return None
    # end time: recorded end, else start + time taken, else start (zero length)
    started["end2"] = started["end"].fillna(
        started["start"] + pd.to_timedelta(started["time"].fillna(0), unit="s"))
    started = started.sort_values("start", kind="stable")
    not_started = df[df["start"].isna()].copy()
    d = pd.concat([started, not_started]).reset_index(drop=True)
    n, n_started = len(d), len(started)
    t0 = started["start"].min()
    off = (started["start"] - t0).dt.total_seconds().tolist() + [0.0] * len(not_started)
    dur = (started["end2"] - started["start"]).dt.total_seconds().tolist() + [0.0] * len(not_started)
    groups = script_groups(df)
    labels = ([f"{l}  {script_title(g)}" for l, g in zip(_bar_labels(d["session"], d["script"]), d["script"])]
              if groups else [short_label(s) for s in d["session"]])
    failed = d["failed"].tolist()
    times = d["time"].tolist()
    statuses = d["status"].tolist()

    ok = [t for t, f in zip(times, failed) if not f]
    vmin, vmax = (min(ok), max(ok)) if ok else (None, None)
    if groups:  # a color per script: min / max of mixed scripts mean nothing
        colors = [FAIL_C if f else _script_color(groups, g) for f, g in zip(failed, d["script"])]
    else:
        colors = [FAIL_C if f else MAX_C if t == vmax else MIN_C if t == vmin else BAR
                  for t, f in zip(times, failed)]

    # peak number of sessions running at the same moment (started ones only)
    run_off, run_dur = off[:n_started], dur[:n_started]
    events = sorted([(o, 1) for o in run_off] + [(o + du, -1) for o, du in zip(run_off, run_dur)],
                    key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, k in events:
        cur += k
        peak = max(peak, cur)
    start_spread = max(run_off)
    total = max(o + du for o, du in zip(run_off, run_dur)) or 1.0
    # a failed session with no duration still gets a short, visible red bar
    min_w = total * 0.012
    dur = [max(du, min_w) if f else du for du, f in zip(dur, failed)]

    height = min(max(4.5, 0.27 * n + 2.2), 60)
    fig, ax = plt.subplots(figsize=(12, height), dpi=150)
    y = list(range(n))
    ax.barh(y, dur, left=off, color=colors, height=0.72)
    fs = 8 if n <= 30 else 7 if n <= 60 else 5
    for i, (yi, o, du, t, f, c) in enumerate(zip(y, off, dur, times, failed, colors)):
        if f:
            # label beside the bar, in red, with why it failed
            reason = " ".join(str(statuses[i]).split())[:70] or "no time recorded"
            took = f" after {t:.2f} seconds" if pd.notna(t) else ""
            txt = (f"FAIL - did not start: {reason}" if i >= n_started
                   else f"FAIL{took} - {reason}")
            ax.text(o + du + total * 0.005, yi, txt, ha="left", va="center", fontsize=fs,
                    color=FAIL_C, fontweight="bold")
            continue
        ax.text(o + du / 2, yi, f"{(t if pd.notna(t) else du):.2f} seconds", ha="center", va="center",
                fontsize=fs, color=_ink_on(c) if groups else "white" if c != BAR else INK,
                fontweight="bold" if c != BAR and not groups else "normal")

    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=9 if n <= 30 else 7 if n <= 60 else 5)
    ax.invert_yaxis()
    ax.set_ylim(n - 0.4, -0.9)
    ax.set_xlim(-total * 0.01, total * (1.3 if any(failed) else 1.02))
    ax.set_xlabel(f"Seconds since first session started "
                  f"({t0.strftime('%d %b %Y, %H:%M:%S')}, timestamps as recorded in the data)")
    ax.set_ylabel("Concurrent session" + ("  and its R script" if groups else "")
                  + "\n(Sessions opened in separate browser tab)")
    ax.grid(axis="x", alpha=0.3)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)

    n_failed = sum(1 for f in failed if f)
    # the script on its own line above the numbers (a full path would overflow it)
    script_info = script_line(test_file, script)
    info = (f"{script_info}\n" if script_info else "") + (f"Sessions: {n}" + (f" ({n_failed} failed)" if n_failed else "") +
            f"   |   Peak running at the same time: {peak}   |   "
            f"Start spread (first to last start): {start_spread:.2f} seconds   |   "
            f"Total test duration: {total:.2f} seconds")
    ax.set_title(f"{title}: start to end timeline", loc="left", fontweight="bold", pad=22 + (13 if script_info else 0))
    ax.text(0, 1.008, info, transform=ax.transAxes, ha="left", va="bottom",
            fontsize=9, color="#555")
    # legend a fixed ~0.9 inch below the axes, under the x-axis label, whatever the chart height
    handles = _script_handles(Patch, groups) if groups else _legend_handles(Patch)
    ax.legend(handles=handles, loc="upper left",
              bbox_to_anchor=(0, -0.9 / height), fontsize=8, frameon=False, ncol=min(len(handles), 4))
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.02, 1, 1))
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return dict(peak_concurrent=peak, start_spread=start_spread, total_duration=total)


def build_histogram_chart(df, out, title, o, tz=REPORT_TZ):
    """Chart 3: distribution of time taken (sessions per time range)."""
    np, plt, _, _ = _plot_libs()
    groups = script_groups(df)
    if groups:
        return _build_grouped_histogram_chart(df, out, title, o, groups, tz=tz)
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
    script_info = script_line(getattr(o, "test_file", ""), getattr(o, "script", ""))
    ax.text(0.01, 0.97, (f"{script_info}\n" if script_info else "") +
            f"Sessions: {len(df)}  (successful: {len(ok)}, failed: {int(df['failed'].sum())})\n"
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


def _build_grouped_histogram_chart(df, out, title, o, groups, tz=REPORT_TZ):
    """Chart 3 for sessions running different scripts: the sessions per time
    range stacked by script, with each script's average marked."""
    np, plt, _, Patch = _plot_libs()
    from matplotlib.lines import Line2D

    good = df[~df["failed"]]
    ok = good["time"].astype(float).to_numpy()
    if _needs_separate_axes(df, groups):
        return _build_histogram_per_script(df, out, title, o, groups, tz=tz)
    nb = int(min(max(np.ceil(np.sqrt(len(ok))), 5), 30))
    edges = np.histogram_bin_edges(ok, bins=nb)
    mids, w = (edges[:-1] + edges[1:]) / 2, edges[1] - edges[0]
    fig, ax = plt.subplots(figsize=(11, 5.8), dpi=150)
    bottom = np.zeros(nb)
    for g in groups:
        times = good.loc[good["script"] == g, "time"].astype(float).to_numpy()
        if not len(times):
            continue
        counts, _ = np.histogram(times, bins=edges)
        color = _script_color(groups, g)
        ax.bar(mids, counts, width=w * 0.92, bottom=bottom, color=color, edgecolor="white", linewidth=0.6)
        bottom += counts
        ax.plot(times.mean(), 0, marker="^", ms=10, color=color, mec=INK, mew=0.8, clip_on=False, zorder=5)
    top = max(bottom.max(), 1)
    for m_, c_ in zip(mids, bottom):
        if c_:
            ax.text(m_, c_ + top * 0.01, f"{c_:.0f}", ha="center", va="bottom", fontsize=9, color=INK)
    handles = _script_handles(Patch, groups, with_failed=False) + [
        Line2D([], [], marker="^", ls="none", color="white", mec=INK, ms=9, label="Script average")]
    if o.threshold is not None:
        ax.axvline(o.threshold, ymax=0.68, color="#8e44ad", ls="-.", lw=1.5)
        handles.append(Line2D([], [], color="#8e44ad", ls="-.", lw=1.5, label=f"Threshold {o.threshold:g} seconds"))
    ax.set_ylim(0, top * 1.75)
    ax.set_xlabel("Time taken to execute script (Seconds)")
    ax.set_ylabel("Number of sessions")
    ax.set_title(f"{title}: distribution of time taken by R script", loc="left", fontweight="bold")
    # beside the axes: the info text spans the top of them
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=9, frameon=False)
    script_info = script_line(getattr(o, "test_file", ""), "")  # the legend names the scripts
    ax.text(0.01, 0.97, (f"{script_info}\n" if script_info else "") +
            f"Sessions: {len(df)}  (successful: {len(ok)}, failed: {int(df['failed'].sum())})\n"
            f"Each bar = sessions whose time falls in a {w:.2f}-second range, split by R script",
            transform=ax.transAxes, va="top", fontsize=9, color="#555")
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(out)
    plt.close(fig)


def _build_histogram_per_script(df, out, title, o, groups, tz=REPORT_TZ):
    """Chart 3 when the scripts' times are too far apart for one axis: a
    histogram per script, each with its own time axis and average marked."""
    np, plt, _, _ = _plot_libs()
    from matplotlib.ticker import MaxNLocator

    good = df[~df["failed"]]
    shown = [g for g in groups if (good["script"] == g).any()]
    cols = min(len(shown), 3)
    rows = -(-len(shown) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 3.6 * rows + 1.2), dpi=150, squeeze=False)
    for ax, g in zip(axes.flat, shown):
        times = good.loc[good["script"] == g, "time"].astype(float).to_numpy()
        nb = int(min(max(np.ceil(np.sqrt(len(times))), 3), 15))
        counts, edges = np.histogram(times, bins=nb)
        mids, w = (edges[:-1] + edges[1:]) / 2, (edges[1] - edges[0]) or 1e-3
        color = _script_color(groups, g)
        ax.bar(mids, counts, width=w * 0.92, color=color, edgecolor="white", linewidth=0.6)
        for m_, c_ in zip(mids, counts):
            if c_:
                ax.text(m_, c_ + max(counts) * 0.02, str(c_), ha="center", va="bottom", fontsize=8, color=INK)
        mean = times.mean()
        ax.axvline(mean, color=AVG_C, ls="--", lw=1.3)
        if o.threshold is not None and edges[0] <= o.threshold <= edges[-1]:
            ax.axvline(o.threshold, color="#8e44ad", ls="-.", lw=1.3)
        ax.set_ylim(0, max(counts) * 1.3)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_title(f"{script_title(g)}  (avg {mean:.2f} s, {len(times)} runs)", loc="left", fontsize=9,
                     fontweight="bold", color=INK)
        ax.set_xlabel("Time taken (Seconds)", fontsize=8)
        ax.tick_params(labelsize=8)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        ax.grid(axis="y", alpha=0.3)
    for ax in list(axes.flat)[len(shown):]:
        ax.axis("off")
    for r in range(rows):
        axes[r][0].set_ylabel("Number of sessions", fontsize=8)
    fig.suptitle(f"{title}: distribution of time taken, per script (own time axis each)", x=0.01, ha="left",
                 fontweight="bold")
    script_info = script_line(getattr(o, "test_file", ""), "")
    fig.text(0.01, 1 - 0.55 / (3.6 * rows + 1.2),
             (f"{script_info}   |   " if script_info else "")
             + f"Sessions: {len(df)}  (successful: {len(good)}, failed: {int(df['failed'].sum())})   |   "
             "dashed line = script average", fontsize=9, color="#555", va="top")
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.025, 1, 1 - 0.8 / (3.6 * rows + 1.2)))
    fig.savefig(out)
    plt.close(fig)


def build_replicates_chart(timings_path, out, title, o, tz=REPORT_TZ):
    """Chart 4: replicates each session completed, from the "Replicates"
    column the timings CSV has when the script writes a run summary (see
    common.script_timings RUN_SUMMARY_COLUMNS). A fixed-duration script
    always takes about the same time, so this - not the time - shows how
    much work each session got done. Returns its stats, or None (no chart)
    when there is no such column or value."""
    np, plt, pd, Patch = _plot_libs()
    raw = pd.read_csv(timings_path)
    if "Replicates" not in raw.columns or "Session Name" not in raw.columns:
        return None
    script = raw["Script"].fillna("").astype(str).str.strip() if "Script" in raw.columns else ""
    df = pd.DataFrame({"session": raw["Session Name"], "script": script,
                       "replicates": pd.to_numeric(raw["Replicates"], errors="coerce")}).dropna()
    if df.empty:
        return None
    groups = script_groups(df)
    df = _group_sorted(df, groups)
    labels = [short_label(s) for s in df["session"]]
    reps = df["replicates"].astype(float).tolist()
    n = len(reps)
    vmin, vmax, mean = min(reps), max(reps), float(np.mean(reps))
    if groups:
        colors = [_script_color(groups, g) for g in df["script"]]
    else:
        colors = [MAX_C if v == vmax else MIN_C if v == vmin else BAR for v in reps]

    # y-range tight around the data (as on the time chart): the differences
    # between sessions are a few percent, invisible on an axis from 0
    span = (vmax - vmin) or max(vmax * 0.05, 1)
    ylo = max(0, vmin - span * 0.6)
    yhi = vmax + span * 0.6

    width = min(max(9, 0.24 * n + 3), 60)
    fig, ax = plt.subplots(figsize=(width, 6.8), dpi=150)
    ax.bar(range(n), [v - ylo for v in reps], bottom=ylo, color=colors, width=0.8 if n > 40 else 0.75)
    lab_fs = 8 if n <= 30 else 7 if n <= 60 else 6 if n <= 120 else 5
    pad = (yhi - ylo) * 0.01
    for xi, v, c in zip(range(n), reps, colors):
        ax.text(xi, v - pad, f"{v:.0f}", rotation=90, ha="center", va="top", fontsize=lab_fs,
                color=_ink_on(c) if groups else "white" if c != BAR else INK)
    ax.axhline(mean, color=AVG_C, ls="--", lw=1.4)
    ax.text(-0.6, mean + pad, f"Average: {mean:.1f} replicates", color=AVG_C, fontsize=10, fontweight="bold",
            va="bottom", bbox=dict(facecolor="white", edgecolor="none", alpha=0.85, pad=1.5))
    ax.set_ylim(ylo, yhi)
    ax.set_xlim(-0.8, n - 0.2)
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, rotation=0 if n <= 30 else 90, fontsize=lab_fs + 1)
    ax.set_xlabel("Concurrent session (Sessions opened in separate browser tab)")
    ax.set_ylabel("Replicates completed" + (f"  (axis starts at {ylo:.0f})" if ylo > 0 else ""))
    ax.set_title(f"{title}: replicates completed per session", loc="left", fontweight="bold", pad=22)
    script_info = script_line(getattr(o, "test_file", ""), getattr(o, "script", ""))
    ax.text(0, 1.008, (f"{script_info}   |   " if script_info else "") +
            f"Sessions: {n}   |   Total: {sum(reps):.0f}   |   Min {vmin:.0f} (green)   "
            f"Max {vmax:.0f} (orange)   |   Spread: {100.0 * (vmax - vmin) / vmax:.1f}% of max",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=9, color="#555")
    ax.legend(handles=_script_handles(Patch, groups, with_failed=False) if groups else [
                  Patch(color=MIN_C, label="Fewest replicates"), Patch(color=MAX_C, label="Most replicates"),
                  Patch(color=BAR, label="Other sessions")], loc="upper right", fontsize=8, frameon=False)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    ax.grid(axis="y", alpha=0.3)
    fig.text(0.01, 0.005, f"Generated: {_now_stamp(tz)}", fontsize=8, color="#666")
    plt.tight_layout(rect=(0, 0.025, 1, 1))
    fig.savefig(out)
    plt.close(fig)
    return {"replicates_min": vmin, "replicates_avg": round(mean, 1), "replicates_max": vmax,
            "replicates_total": sum(reps)}


def generate_session_time_report(timings_path, title="Time taken per concurrent session", threshold=None,
                                 test_name="", environment="", browser="", notes="", sort="session",
                                 tz=REPORT_TZ, attach=True, script="", test_file=None):
    """Build the session, histogram and timeline charts from `timings_path`
    as <timings_path without extension>.png / _histogram.png / _timeline.png
    (plus _replicates.png when the CSV has a Replicates column) and attach
    them to Allure (when `attach`). `script` (the R script the
    sessions ran) is shown as "Path" in the header of all three, next to
    "Script name": `test_file`, by default the running test's file. Returns
    the stats dict (plus "charts": the PNG paths), or None if the report
    could not be made.
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
                        browser=browser, notes=notes, script=script,
                        test_file=_running_test_file() if test_file is None else test_file)
    base = os.path.splitext(timings_path)[0]
    charts = []
    result = None
    try:
        df = load_session_times(timings_path)
        result = build_session_chart(df, base + ".png", title, o, sort=sort, tz=tz)
        charts.append(base + ".png")
        build_histogram_chart(df, base + "_histogram.png", title, o, tz=tz)
        charts.append(base + "_histogram.png")
        timeline = build_timeline_chart(df, base + "_timeline.png", title, tz=tz, script=script,
                                        test_file=o.test_file)
        if timeline:
            result.update(timeline)
            charts.append(base + "_timeline.png")
        replicates = build_replicates_chart(timings_path, base + "_replicates.png", title, o, tz=tz)
        if replicates:
            result.update(replicates)
            charts.append(base + "_replicates.png")
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
    p.add_argument("--script", default="", help="path of the R script the sessions ran, shown on every chart")
    p.add_argument("--test-file", default="", help="test file that ran it (Script name), shown on every chart")
    a = p.parse_args()
    r = generate_session_time_report(a.input, a.title, a.threshold, a.test_name, a.environment, a.browser,
                                     a.notes, a.sort, a.tz, attach=False, script=a.script,
                                     test_file=a.test_file)
    if r is None:
        raise SystemExit(1)
    for k, v in r.items():
        if k == "per_script":
            for script, s in v.items():
                print(f"  {script}: " + ", ".join(f"{sk} {sv:.2f}" if isinstance(sv, float) else f"{sk} {sv}"
                                                  for sk, sv in s.items()))
            continue
        print(f"  {k}: {v:.2f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == "__main__":
    main()
