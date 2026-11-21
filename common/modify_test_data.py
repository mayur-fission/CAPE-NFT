#!/usr/bin/env python3
"""Change a share of the test data in place, for the snapshot "modify" scenario.

Usage (on the session's server, from the RStudio console via bash):

  python3 modify_test_data.py /path/to/data/folder [--percent 10] [--seed N]

Picks files at random (seeded, so a run can be repeated) until the files picked
hold at least --percent of the folder's bytes, and overwrites each one's contents
with new random bytes, keeping its size and name. Hidden files (the generator's
.make-client-shape.json marker) are never touched. The content is fresh every run
(os.urandom), so a second run on the same files still changes them.

Prints one JSON line: {"files_changed", "bytes_changed", "files_total",
"bytes_total", "percent", "seed", "seconds"}. Exits 1 if there is nothing to change.
Standard library only; Python 3.6+.
"""
import argparse
import json
import os
import random
import sys
import time

CHUNK = 8 * 1024 * 1024  # bytes written at a time


def data_files(folder):
    """(path, size) of every non-hidden regular file under `folder`, sorted by path."""
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in files:
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                found.append((path, os.path.getsize(path)))
    return sorted(found)


def pick(files, percent, seed):
    """[(path, bytes to overwrite)] in a seeded random order, adding up to
    `percent` of all the bytes: whole files, and the first part of the last
    one picked, so a large file does not push the share past the target."""
    total = sum(size for _, size in files)
    target = int(round(total * percent / 100.0))
    order = list(files)
    random.Random(seed).shuffle(order)
    picked, picked_bytes = [], 0
    for path, size in order:
        left = target - picked_bytes
        if left <= 0:
            break
        if size == 0:
            continue
        n = min(size, left)
        picked.append((path, n))
        picked_bytes += n
    return picked, picked_bytes, total


def overwrite(path, size):
    """Overwrite the first `size` bytes of `path` with new random bytes
    (the file keeps its size and inode)."""
    with open(path, "r+b") as fh:
        done = 0
        while done < size:
            n = min(CHUNK, size - done)
            fh.write(os.urandom(n))
            done += n
        fh.flush()
        os.fsync(fh.fileno())


def main(argv=None):
    ap = argparse.ArgumentParser(description="Overwrite a share of the test data with new random bytes.")
    ap.add_argument("folder", help="the test data folder (made by make_client_shape.py)")
    ap.add_argument("--percent", type=float, default=10.0, help="share of the bytes to change (default 10)")
    ap.add_argument("--seed", type=int, default=None, help="seed for which files are picked (default: random)")
    args = ap.parse_args(argv)
    if not 0 < args.percent <= 100:
        ap.error("--percent must be more than 0 and at most 100")
    if not os.path.isdir(args.folder):
        print("no such folder: %s" % args.folder, file=sys.stderr)
        return 1
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 1000000)

    files = data_files(args.folder)
    picked, picked_bytes, total = pick(files, args.percent, seed)
    if not picked:
        print("nothing to change in %s" % args.folder, file=sys.stderr)
        return 1
    started = time.monotonic()
    for path, size in picked:
        overwrite(path, size)
    print(json.dumps({"files_changed": len(picked), "bytes_changed": picked_bytes, "files_total": len(files),
                      "bytes_total": total, "percent": args.percent, "seed": seed,
                      "seconds": round(time.monotonic() - started, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
