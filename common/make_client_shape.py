#!/usr/bin/env python3
"""Make snapshot test data in the shape of the client's representative project, at any size.

The shape comes from the client's size census of 16 Nov 2026 (156,911 files, 269.93 GB). Every
size band of the census is present, and the file count and the bytes of each band are scaled to
the requested total, so the proportions stay those of the census. Files of 512,000 bytes or more
are the ones CoActive sends to DVC, smaller ones go to git.

Usage:
  make_client_shape.py 10GB /path/to/empty/folder
  make_client_shape.py 250GB --dry-run
  make_client_shape.py 2.5 /path/to/folder --seed 7

Sizes are decimal gigabytes (1 GB = 1,000,000,000 bytes), as in the census. The smallest size
accepted is 1 GB, because below that several bands would hold well under one file in proportion.

The same size and seed always give the same tree, byte for byte. Re-running on a folder this
script made finishes an interrupted run and leaves complete files alone.

Exit codes: 0 done, 1 failed, refused or incomplete, 2 usage error.

Python 3.9 or later, standard library only.
"""

import argparse
import errno
import hashlib
import json
import os
import random
import re
import shutil
import signal
import stat
import sys
import time

TOOL = "make_client_shape"
VERSION = 1
MARKER = ".make-client-shape.json"
PART = ".mcs-part"  # suffix of a file being written, renamed into place when complete
SKIP = ".snapshot"  # never entered, never written
DVC_AT = 512_000  # files this size or larger go to DVC (DVC_THRESHOLD_KB default 500 x 1024)
GB = 1_000_000_000
CHUNK = 4 << 20
MAX_GB = 1000  # 1 TB, four times the client's representative project

# Client census, 16 Nov 2026: (band, files, GB, smallest bytes, largest bytes, content style).
# The smallest and largest bytes are the ranges of CLIENT_BANDS in testdata.py. The bytes per
# band come from the census, so each band's average file size is the census average.
CENSUS_FILES = 156_911
CENSUS_GB = 269.93
BANDS = [
    ("under 10 KB", 105_804, 0.19, 256, 3_800, "text"),
    ("10 to 100 KB", 41_887, 1.32, 10_240, 53_000, "text"),
    ("100 to 200 KB", 3_434, 0.43, 102_400, 150_000, "csv"),
    ("200 to 400 KB", 565, 0.18, 204_800, 409_599, "csv"),
    ("400 to 500 KB", 311, 0.14, 409_600, 511_999, "csv"),
    ("500 KB to 1 MB", 930, 0.62, 512_000, 825_000, "csv"),
    ("1 to 10 MB", 2_728, 16.70, 2_000_000, 10_240_000, "csv"),
    ("10 to 100 MB", 202, 12.03, 20_000_000, 100_000_000, "bin"),
    ("100 MB to 1 GB", 1_050, 238.33, 104_857_600, 350_000_000, "bin"),
]
BAND_GB = sum(b[2] for b in BANDS)  # 269.94, the rounded band figures
# Upper edge of each census band, used to sort files found on disk into bands.
EDGES = [10_240, 102_400, 204_800, 409_600, 512_000, 1_048_576, 10_485_760, 104_857_600, 1_073_741_824]

_WORDS = (
    "THETA OMEGA SIGMA ETA CL V KA F1 ALAG TVCL TVV IIV WT AGE CRCL DOSE AMT DV PRED IPRED CWRES "
    "NONMEM PsN Monolix mrgsolve ADVAN TRANS SUBROUTINE ESTIMATION METHOD COV TABLE NOPRINT "
    "ggplot dplyr library read_csv mutate filter summarise model run scenario cohort bootstrap"
).split()


class Refused(Exception):
    pass


# --------------------------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------------------------


def parse_size(text):
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(gb)?\s*", text, re.IGNORECASE)
    if not m:
        raise argparse.ArgumentTypeError(f"size {text!r} is not a number of GB, for example 10GB or 2.5")
    gb = float(m.group(1))
    if gb < 1:
        raise argparse.ArgumentTypeError("the smallest size is 1GB, the shape cannot be kept below that")
    if gb > MAX_GB:
        raise argparse.ArgumentTypeError(f"the largest size is {MAX_GB}GB")
    return gb


def plan(size_gb, seed):
    """Return (bands, files). bands: per band dict of targets. files: list of (path, size, style)."""
    scale = size_gb / CENSUS_GB
    bands, files = [], []
    for b, (name, n_census, gb_census, lo, hi, style) in enumerate(BANDS):
        want_files = n_census * scale
        want_bytes = size_gb * GB * gb_census / BAND_GB  # band sums are rounded, so normalise to the total
        n = max(1, round(want_files))
        mean = min(max(want_bytes / n, lo), hi)
        half = min(mean - lo, hi - mean)  # spread evenly around the mean, inside the band range
        rng = random.Random(f"{TOOL}|{seed}|band{b}")
        offsets = [2 * (i + 0.5) / n - 1 for i in range(n)]
        rng.shuffle(offsets)
        sizes = [int(round(mean + half * d)) for d in offsets]
        sizes[-1] = min(max(sizes[-1] + int(round(mean * n)) - sum(sizes), lo), hi)
        for i, size in enumerate(sizes):
            folder = f"runs/run{i // 200:04d}" if hi < DVC_AT else f"data/band{b}/d{i // 50:04d}"
            ext = {"bin": "bin", "csv": "csv", "text": "txt"}[style]
            files.append((f"{folder}/b{b}_{i:06d}.{ext}", size, style))
        bands.append({"band": name, "want_files": want_files, "want_bytes": want_bytes,
                      "files": n, "bytes": sum(sizes)})
    return bands, files


def band_of(size):
    for b, edge in enumerate(EDGES):
        if size < edge:
            return b
    return len(EDGES) - 1


def pct(got, want):
    return f"{100 * (got - want) / want:+.1f}%" if want else "n/a"


def print_table(title, rows, total_want_files, total_want_bytes):
    print(title)
    print(f"  {'band':<16}{'target files':>14}{'files':>9}{'error':>9}{'target GB':>12}{'GB':>10}{'error':>9}")
    tf = tb = 0
    for r in rows:
        tf += r["files"]
        tb += r["bytes"]
        print(f"  {r['band']:<16}{r['want_files']:>14,.1f}{r['files']:>9,}{pct(r['files'], r['want_files']):>9}"
              f"{r['want_bytes'] / GB:>12.4f}{r['bytes'] / GB:>10.4f}{pct(r['bytes'], r['want_bytes']):>9}")
    print(f"  {'total':<16}{total_want_files:>14,.1f}{tf:>9,}{pct(tf, total_want_files):>9}"
          f"{total_want_bytes / GB:>12.4f}{tb / GB:>10.4f}{pct(tb, total_want_bytes):>9}")
    return tf, tb


def print_split(git_files, git_bytes, dvc_files, dvc_bytes):
    n, s = git_files + dvc_files, git_bytes + dvc_bytes
    print(f"  git side (under {DVC_AT:,} bytes): {git_files:,} files ({100 * git_files / max(n, 1):.1f}%), "
          f"{git_bytes / GB:.4f} GB ({100 * git_bytes / max(s, 1):.2f}%)")
    print(f"  DVC side ({DVC_AT:,} bytes or more): {dvc_files:,} files ({100 * dvc_files / max(n, 1):.1f}%), "
          f"{dvc_bytes / GB:.4f} GB ({100 * dvc_bytes / max(s, 1):.2f}%)")
    cf, cb = sum(b[1] for b in BANDS[:5]), sum(b[2] for b in BANDS[:5])
    tb = sum(b[2] for b in BANDS)
    print(f"  census: git {100 * cf / CENSUS_FILES:.1f}% of files and {100 * cb / tb:.2f}% of bytes, "
          f"DVC {100 - 100 * cf / CENSUS_FILES:.1f}% of files and {100 - 100 * cb / tb:.2f}% of bytes")


# --------------------------------------------------------------------------------------------
# Content: every byte comes from the seed, the path and the size
# --------------------------------------------------------------------------------------------


def _pool(kind):
    lines = []
    for i in range(4096):
        b = hashlib.shake_256(f"pool:{kind}:{i}".encode()).digest(24)
        if kind == "text":
            lines.append(" ".join(_WORDS[x % len(_WORDS)] for x in b[: 6 + b[0] % 12]) + "\n")
        else:
            lines.append(",".join(f"{int.from_bytes(b[j:j + 3], 'big') / 997.0:.4f}" for j in range(0, 18, 3)) + "\n")
    return lines


_POOLS = {}


def chunks(seed, path, size, style):
    """Yield the file's bytes in pieces of about CHUNK bytes."""
    key = f"{TOOL}|{seed}|{path}"
    if style == "bin":
        done, k = 0, 0
        while done < size:
            n = min(CHUNK, size - done)
            yield hashlib.shake_256(f"{key}|{k}".encode()).digest(n)
            done += n
            k += 1
        return
    if style not in _POOLS:
        lines = _pool(style)
        _POOLS[style] = (lines, sum(map(len, lines)) / len(lines))
    pool, avg = _POOLS[style]
    rng = random.Random(key)
    head = f"# {path} seed {seed}\n" + ("" if style == "text" else "ID,TIME,DV,PRED,IPRED,CWRES\n")
    out = head.encode()
    left = size
    while left > 0:
        if len(out) < min(left, CHUNK):
            need = int((min(left, CHUNK) - len(out)) / avg) + 8
            out += "".join(rng.choices(pool, k=need)).encode()
            continue
        piece, out = out[:min(left, CHUNK)], out[min(left, CHUNK):]
        left -= len(piece)
        yield piece


# --------------------------------------------------------------------------------------------
# Writing, without following symlinks
# --------------------------------------------------------------------------------------------

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def open_dir(root_fd, rel):
    """Open (creating where missing) folder `rel` below root_fd, one level at a time, no symlinks."""
    fd = os.dup(root_fd)
    try:
        for part in rel.split("/"):
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nfd = os.open(part, DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise Refused(f"{rel}: {part} exists and is not a real folder (a symlink or a file)")
                raise
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def complete(dir_fd, name, size):
    """True when name is already a regular file of the planned size (written by an earlier run)."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if stat.S_ISDIR(st.st_mode):
        raise Refused(f"{name} is a folder where a file is planned")
    return stat.S_ISREG(st.st_mode) and st.st_size == size


def write_file(dir_fd, name, data_iter):
    """Write to name + PART, then rename over name, so a file is either absent or complete."""
    part = name + PART
    try:
        os.unlink(part, dir_fd=dir_fd)  # left behind by an interrupted run
    except FileNotFoundError:
        pass
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=dir_fd)
        with os.fdopen(fd, "wb", closefd=True) as f:
            for piece in data_iter:
                f.write(piece)
        os.rename(part, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(part, dir_fd=dir_fd)  # absent after a successful rename, so this never removes name
        except OSError:
            pass
        raise


def write_marker(root_fd, info):
    data = (json.dumps(info, indent=2) + "\n").encode()
    write_file(root_fd, MARKER, iter([data]))


def check_target(target, size_gb, seed):
    """Return the marker found, None for an empty folder, or "new" for a folder still to create."""
    if SKIP in os.path.abspath(target).split(os.sep):
        raise Refused(f"{target} is inside a {SKIP} folder")
    if not os.path.lexists(target):
        parent = os.path.dirname(target)
        if os.path.islink(parent) or not os.path.isdir(parent):
            raise Refused(f"{target} does not exist and its parent {parent} is not a real folder")
        return "new"
    if os.path.islink(target) or not os.path.isdir(target):
        raise Refused(f"{target} is not a real folder (a file or a symlink)")
    # a run killed while writing its first marker leaves only the marker's part file: still empty
    names = [n for n in os.listdir(target) if n not in (SKIP, MARKER + PART)]
    if not names:
        return None
    if MARKER not in names:
        raise Refused(f"{target} is not empty and holds no {MARKER}, so this script did not make it. "
                      "Use an empty folder.")
    mpath = os.path.join(target, MARKER)
    if os.path.islink(mpath):
        raise Refused(f"{mpath} is a symlink")
    try:
        with open(mpath) as f:
            found = json.load(f)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read {mpath}: {exc}")
    if not isinstance(found, dict):
        raise Refused(f"{mpath} is not a marker written by this script")
    if found.get("tool") != TOOL or found.get("size_gb") != size_gb or found.get("seed") != seed:
        raise Refused(f"{target} was made with size {found.get('size_gb')} GB and seed {found.get('seed')}, "
                      f"not {size_gb} GB and seed {seed}. Use an empty folder for a different size or seed.")
    return found


# --------------------------------------------------------------------------------------------
# Census of what is on disk
# --------------------------------------------------------------------------------------------


def census(target):
    """Count regular files per band, never following symlinks or entering .snapshot."""
    files = [0] * len(BANDS)
    sizes = [0] * len(BANDS)
    other = 0
    stack = [target]
    while stack:
        d = stack.pop()
        with os.scandir(d) as it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    if e.name != SKIP:
                        stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    if d == target and e.name == MARKER:
                        continue
                    if e.name.endswith(PART):
                        other += 1
                        continue
                    s = e.stat(follow_symlinks=False).st_size
                    b = band_of(s)
                    files[b] += 1
                    sizes[b] += s
                else:
                    other += 1
    return files, sizes, other


def report_census(target, size_gb, bands):
    files, sizes, other = census(target)
    scale = size_gb / CENSUS_GB
    rows = [{"band": BANDS[b][0], "want_files": BANDS[b][1] * scale, "want_bytes": size_gb * GB * BANDS[b][2] / BAND_GB,
             "files": files[b], "bytes": sizes[b]} for b in range(len(BANDS))]
    print()
    print_table(f"Census of {target} (files sorted by their size on disk, marker excluded), "
                "against the census proportions", rows, CENSUS_FILES * scale, sum(r["want_bytes"] for r in rows))
    git_f = sum(files[:5])
    git_b = sum(sizes[:5])
    print_split(git_f, git_b, sum(files[5:]), sum(sizes[5:]))
    if other:
        print(f"  also found {other} symlinks, special files or unfinished {PART} files")
    return files, sizes


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make snapshot test data in the client's census shape.")
    ap.add_argument("size", type=parse_size, help="total size in decimal GB: 1GB, 10GB, 250GB or a plain number")
    ap.add_argument("target", nargs="?", help="folder to fill, empty or made earlier by this script")
    ap.add_argument("--seed", type=int, default=1, help="seed for sizes and content (default 1)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan per size band and write nothing")
    args = ap.parse_intermixed_args(argv)  # Python 3.9 loses the folder after an option without this
    if args.target is None and not args.dry_run:
        ap.error("a target folder is needed unless --dry-run is given")

    bands, files = plan(args.size, args.seed)
    want_bytes = sum(r["want_bytes"] for r in bands)
    print(f"{TOOL} version {VERSION}: {args.size:g} GB, seed {args.seed}")
    tf, tb = print_table("Plan per size band (target is the census scaled to this size)", bands,
                         CENSUS_FILES * args.size / CENSUS_GB, want_bytes)
    git = [s for _, s, _ in files if s < DVC_AT]
    print_split(len(git), sum(git), len(files) - len(git), tb - sum(git))

    if args.target is None:
        return 0
    target = os.path.abspath(args.target)
    try:
        found = check_target(target, args.size, args.seed)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        if found == "new":
            state = "it does not exist yet and would be created"
        elif found is None:
            state = "empty"
        else:
            state = "made earlier by this script, a re-run would finish it"
        print(f"\ndry run: {target} is acceptable ({state}). Nothing written.")
        return 0

    free = shutil.disk_usage(target if found != "new" else os.path.dirname(target)).free
    if not isinstance(found, dict) and free < tb:
        print(f"refused: {target} has {free / GB:.2f} GB free, the plan needs {tb / GB:.2f} GB", file=sys.stderr)
        return 1

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)  # a container stop sends SIGTERM: clean up the same way as Ctrl+C
    signal.signal(signal.SIGINT, stop)  # also when started in the background, where SIGINT is ignored
    try:
        if found == "new":
            os.mkdir(target, 0o755)
        root_fd = os.open(target, DIR_FLAGS)
    except OSError as exc:
        print(f"refused: cannot open {target}: {exc}", file=sys.stderr)
        return 1
    info = {"tool": TOOL, "version": VERSION, "size_gb": args.size, "seed": args.seed,
            "files": tf, "bytes": tb, "complete": False}
    written = skipped = 0
    wrote_bytes = 0
    failures = []
    start = last = time.monotonic()
    try:
        write_marker(root_fd, info)
        by_folder = {}
        for path, size, style in files:
            by_folder.setdefault(path.rsplit("/", 1)[0], []).append((path, size, style))
        done_bytes = 0
        for folder in sorted(by_folder):
            try:
                dfd = open_dir(root_fd, folder)
            except Refused as exc:
                failures.append(str(exc))
                continue
            except OSError as exc:
                failures.append(f"{folder}: {exc}")
                continue
            try:
                for path, size, style in by_folder[folder]:
                    name = path.rsplit("/", 1)[1]
                    try:
                        if complete(dfd, name, size):
                            skipped += 1
                        else:
                            write_file(dfd, name, chunks(args.seed, path, size, style))
                            written += 1
                            wrote_bytes += size
                    except (Refused, OSError) as exc:
                        failures.append(f"{path}: {exc}")
                        if getattr(exc, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
                            raise  # the volume is full: stop rather than fail every remaining file
                    done_bytes += size
                    now = time.monotonic()
                    if now - last >= 10:
                        last = now
                        rate = wrote_bytes / max(now - start, 1e-9) / 1e6
                        print(f"  {100 * done_bytes / tb:5.1f}% of bytes, {written + skipped:,} of {tf:,} files, "
                              f"writing at {rate:,.0f} MB/s", flush=True)
            finally:
                os.close(dfd)
    except KeyboardInterrupt:
        print("\ninterrupted: run the same command again to finish", file=sys.stderr)
        os.close(root_fd)
        return 1
    except (Refused, OSError) as exc:
        if getattr(exc, "errno", None) in (errno.ENOSPC, errno.EDQUOT):
            failures.append(f"the volume is full or over quota, stopped: {exc}")
        elif str(exc) not in failures[-1:]:
            failures.append(str(exc))

    secs = time.monotonic() - start
    print(f"\nwrote {written:,} files ({wrote_bytes / GB:.3f} GB) in {secs:,.1f} s, "
          f"{wrote_bytes / max(secs, 1e-9) / 1e6:,.0f} MB/s, and {skipped:,} files were already complete")
    try:
        files_on_disk, _ = report_census(target, args.size, bands)
    except OSError as exc:
        failures.append(f"census could not read the folder: {exc}")
        files_on_disk = [0] * len(BANDS)
    for f in failures[:20]:
        print(f"failed: {f}", file=sys.stderr)
    if len(failures) > 20:
        print(f"... and {len(failures) - 20} more failures", file=sys.stderr)
    missing = [BANDS[b][0] for b in range(len(BANDS)) if files_on_disk[b] < bands[b]["files"]]
    if missing and not failures:
        failures.append("fewer files on disk than planned in band(s): " + ", ".join(missing))
        print(f"failed: {failures[-1]}", file=sys.stderr)
    if failures:
        print(f"INCOMPLETE: {len(failures)} item(s) failed. Fix the cause and run the same command again.", file=sys.stderr)
        os.close(root_fd)
        return 1
    try:
        info["complete"] = True  # only after the census found every planned file
        write_marker(root_fd, info)
    except OSError as exc:
        print(f"failed: cannot update the marker: {exc}", file=sys.stderr)
        return 1
    finally:
        os.close(root_fd)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
