"""Test data for the project snapshot timing test, made with
common/make_client_shape.py: files in the shape of the client's
representative project (its size census), at any size from 1 GB. It runs on
the session's server from the RStudio console, as

    python3 make_client_shape.py <N>GB /path/to/empty/folder

Flow: check the data folder is empty or absent (SNAPSHOT_DATA_CLEAN=1
      empties it first) -> put the script on the server -> from the RStudio
      console run  cd <script folder> && python3 make_client_shape.py <N>GB
      <data folder>  (bash via R system2(), fsync_setup.run_shell()) -> read
      the script's marker file (.make-client-shape.json) for the files and
      bytes written

Which script (.env):
  SNAPSHOT_DATA_SERVER_SCRIPT - already on the server: run as is
  SNAPSHOT_DATA_SCRIPT        - a local script, uploaded to
                                SNAPSHOT_SCRIPT_DIR first (default
                                common/make_client_shape.py; a relative
                                path is from the repo root)
Either takes `<N>GB <folder> [--seed N]` and writes the marker file, like
make_client_shape.py.

The script is never put in the data folder: make_client_shape.py only
fills an empty folder. The same size and seed give the same bytes, and on a
folder it filled before with them it writes nothing - so the folder must
start empty for a run to create its data.
"""
import json
import os
import posixpath
import shlex
import time
from typing import Optional

from common.fsync_setup import run_shell, upload_file
from common.step_log import log_step
from config.config import REPO_ROOT, env

DEFAULT_GENERATOR_SCRIPT = os.path.join(REPO_ROOT, "common", "make_client_shape.py")
# Where the script is uploaded: the user's home, outside the project (or it would be in the
# snapshot) and outside the shared /mnt/fsx root (RSTUDIO_WORKING_DIR), where it may not be writable
DEFAULT_SCRIPT_DIR = "~/snapshot_nfr"
# The modify scenario's script: overwrites a share of the data in place
MODIFY_SCRIPT = os.path.join(REPO_ROOT, "common", "modify_test_data.py")
# The generator's output, written next to the script (never in the data folder)
GENERATOR_LOG = "make_client_shape.log"
# The interpreter the script is run with on the server
PYTHON = "python3"
# make_client_shape.MARKER: {"files", "bytes", "size_gb", "seed", "complete", ...}
MARKER = ".make-client-shape.json"
# SNAPSHOT_DATA_CLEAN refuses folders less deep than this (/, /mnt, /mnt/fsx)
MIN_CLEAN_DEPTH = 3


class TestDataError(RuntimeError):
    """The test data could not be created."""
    __test__ = False  # not a pytest test class


def local_generator_script() -> str:
    """SNAPSHOT_DATA_SCRIPT (relative paths from the repo root), else
    common/make_client_shape.py. Raises TestDataError if it is missing."""
    path = env("SNAPSHOT_DATA_SCRIPT") or DEFAULT_GENERATOR_SCRIPT
    path = path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)
    if not os.path.isfile(path):
        raise TestDataError("data generator script not found: %s (SNAPSHOT_DATA_SCRIPT)" % path)
    return path


def _shell(page, command: str, what: str, timeout_ms: int = 300000) -> str:
    """run_shell(command); its output, or TestDataError naming `what` when
    it exits non-zero."""
    status, output = run_shell(page, command, timeout_ms=timeout_ms)
    if status != 0:
        raise TestDataError("%s failed (exit %d): $ %s -> %s" % (what, status, command, output[-800:]))
    return output


def prepare_empty_folder(page, data_dir: str, clean: bool = False):
    """Make sure `data_dir` is empty or absent; with clean=True, delete what
    is in it (the folder itself stays). Raises TestDataError if it holds
    files and clean is False."""
    q = shlex.quote(data_dir)
    found = _shell(page, "if [ -e %s ]; then ls -A %s | head -5; fi" % (q, q), "checking %s" % data_dir)
    if not found:
        return
    if not clean:
        raise TestDataError("%s is not empty (%s ...): make_client_shape.py needs an empty folder. Empty it, set "
                            "SNAPSHOT_DATA_DIR to a new folder, or set SNAPSHOT_DATA_CLEAN=1 to let the test "
                            "empty it." % (data_dir, found))
    depth = len([p for p in posixpath.normpath(data_dir).split("/") if p])
    if depth < MIN_CLEAN_DEPTH:
        raise TestDataError("SNAPSHOT_DATA_CLEAN: refusing to empty %s, a top-level folder" % data_dir)
    log_step("Emptying %s (SNAPSHOT_DATA_CLEAN)", data_dir)
    _shell(page, "find %s -mindepth 1 -delete" % q, "emptying %s" % data_dir, timeout_ms=1800000)


def server_home(page) -> str:
    """The session user's home folder on the server ($HOME)."""
    home = _shell(page, 'printf "%s" "$HOME"', "reading $HOME").strip()
    if not home.startswith("/"):
        raise TestDataError("unexpected $HOME on the server: %r" % home)
    return home


def expand_home(path: str, home: str) -> str:
    """`path` with a leading ~ replaced by `home` (shell quoting stops ~
    being expanded)."""
    return home + path[1:] if path == "~" or path.startswith("~/") else path


def generator_on_server(page, script_dir: str, server_script: Optional[str] = None) -> str:
    """Absolute server path of the generator: `server_script` (default
    SNAPSHOT_DATA_SERVER_SCRIPT) if set and present, else the
    local_generator_script() uploaded to `script_dir` (~ allowed)."""
    home = server_home(page)
    script = server_script or env("SNAPSHOT_DATA_SERVER_SCRIPT")
    if script:
        script = expand_home(script, home)
        _shell(page, "test -f %s" % shlex.quote(script),
               "looking for the data generator %s (SNAPSHOT_DATA_SERVER_SCRIPT) on the server" % script)
        log_step("Using the data generator on the server: %s", script)
        return script
    return upload_tool(page, local_generator_script(), script_dir, home)


def upload_tool(page, local_path: str, script_dir: str, home: Optional[str] = None) -> str:
    """Upload the local script `local_path` to the server folder
    `script_dir` (~ allowed; created if missing); its absolute server path."""
    home = home or server_home(page)
    abs_dir = expand_home(script_dir, home)
    _shell(page, "mkdir -p %s" % shlex.quote(abs_dir), "creating %s" % abs_dir)
    # The Files pane and its upload dialog show home folders as ~/..., so upload to that form
    upload_dir = "~" + abs_dir[len(home):] if abs_dir == home or abs_dir.startswith(home + "/") else abs_dir
    log_step("Uploading %s to %s", local_path, upload_dir)
    return expand_home(upload_file(page, local_path, upload_dir), home)


def create_test_data(page, data_dir: str, size_gb: float, timeout_ms: int = 1800000,
                     seed: Optional[int] = None, clean: bool = False,
                     script_dir: Optional[str] = None, server_script: Optional[str] = None) -> dict:
    """Fill `data_dir` (empty or absent; its parent must exist) on the
    server of the session `page` shows (console ready) with `size_gb`
    decimal GB (at least 1) in the client's shape:

        cd <script folder> && python3 make_client_shape.py <size_gb>GB <data_dir> [--seed <seed>]

    The script is uploaded to `script_dir` (default SNAPSHOT_SCRIPT_DIR,
    else ~/snapshot_nfr, outside the project) unless a
    server script is set (see generator_on_server()). Returns {"folder",
    "files", "total_bytes", "size_gb", "seed", "seconds"}. Raises
    TestDataError if a step fails."""
    data_dir = data_dir.rstrip("/")
    prepare_empty_folder(page, data_dir, clean)
    script_dir = script_dir or env("SNAPSHOT_SCRIPT_DIR") or DEFAULT_SCRIPT_DIR
    script = generator_on_server(page, script_dir, server_script)

    command = "cd %s && %s %s %gGB %s" % (shlex.quote(posixpath.dirname(script)), PYTHON,
                                         shlex.quote(posixpath.basename(script)), size_gb, shlex.quote(data_dir))
    if seed is not None:
        command += " --seed %d" % seed
    # The script's output (plan table, progress) goes to a log next to the script, not the
    # console: run_shell() returns it as one console line, and RStudio cuts lines over 1000
    # characters - the result marker after it would never show and the test would wait forever.
    log_path = posixpath.join(posixpath.dirname(script), GENERATOR_LOG)
    log_step("Generating %g GB of test data: $ %s  (output in %s)", size_gb, command, log_path)
    quoted_log = shlex.quote(log_path)
    started = time.monotonic()
    tail = _shell(page, "%s > %s 2>&1; st=$?; tail -n 3 %s | cut -c1-200; exit $st"
                  % (command, quoted_log, quoted_log), "test data generation (log: %s)" % log_path,
                  timeout_ms=timeout_ms)
    seconds = round(time.monotonic() - started, 1)
    log_step("Generator finished in %.1fs: %s", seconds, tail)

    marker = read_marker(page, data_dir)
    if marker is None:
        raise TestDataError("the generator wrote no %s in %s" % (MARKER, data_dir))
    if not marker.get("complete"):
        raise TestDataError("%s says the data is not complete: %r" % (MARKER, marker))

    summary = {"action": "add", "folder": data_dir, "files": marker["files"], "total_bytes": marker["bytes"],
               "size_gb": marker["size_gb"], "seed": marker["seed"], "seconds": seconds}
    log_step("Test data ready: %d file(s), %.2f GB in %s (%.1fs)", summary["files"],
             summary["total_bytes"] / 1e9, data_dir, seconds)
    return summary


def read_marker(page, data_dir: str) -> Optional[dict]:
    """The generator's marker file in `data_dir` ({"files", "bytes",
    "size_gb", "seed", "complete", ...}), or None when there is none."""
    marker_path = posixpath.join(data_dir, MARKER)
    q = shlex.quote(marker_path)
    raw = _shell(page, "if [ -f %s ]; then cat %s; fi" % (q, q), "reading %s" % marker_path)
    if not raw.strip():
        return None
    try:
        return json.loads(raw.replace(" | ", "\n"))
    except ValueError as exc:
        raise TestDataError("cannot parse %s: %r" % (marker_path, raw[:300])) from exc


def modify_test_data(page, data_dir: str, percent: float = 10.0, seed: Optional[int] = None,
                     timeout_ms: int = 1800000, script_dir: Optional[str] = None) -> dict:
    """Overwrite `percent` of the bytes of the test data in `data_dir` with
    new random bytes, in place (files keep their names and sizes), with
    common/modify_test_data.py uploaded to `script_dir` (default
    SNAPSHOT_SCRIPT_DIR, else ~/snapshot_nfr) and run from the console:

        cd <script folder> && python3 modify_test_data.py <data_dir> --percent <p> [--seed <seed>]

    Files are picked at random (seeded; the seed used is returned) until
    they hold `percent` of the bytes - the last one only in part. Only a
    folder make_client_shape.py made (its marker file is there) is changed.
    Returns {"action": "modify", "folder", "files", "total_bytes",
    "size_gb", "seed", "seconds", "percent", "files_total", "bytes_total"}:
    files/total_bytes are what was changed. Raises TestDataError if a step fails."""
    data_dir = data_dir.rstrip("/")
    if read_marker(page, data_dir) is None:
        raise TestDataError("%s has no test data (no %s): run the add scenario first "
                            "(test_snapshot_after_adding_data)" % (data_dir, MARKER))
    script = upload_tool(page, MODIFY_SCRIPT, script_dir or env("SNAPSHOT_SCRIPT_DIR") or DEFAULT_SCRIPT_DIR)
    command = "cd %s && %s %s %s --percent %g" % (shlex.quote(posixpath.dirname(script)), PYTHON,
                                                 shlex.quote(posixpath.basename(script)), shlex.quote(data_dir),
                                                 percent)
    if seed is not None:
        command += " --seed %d" % seed
    log_step("Changing %g%% of the test data: $ %s", percent, command)
    started = time.monotonic()
    out = _shell(page, command, "changing the test data", timeout_ms=timeout_ms)
    seconds = round(time.monotonic() - started, 1)
    try:
        result = json.loads(out.split(" | ")[-1])
    except ValueError as exc:
        raise TestDataError("cannot read what modify_test_data.py changed: %r" % out[-300:]) from exc
    summary = {"action": "modify", "folder": data_dir, "files": result["files_changed"],
               "total_bytes": result["bytes_changed"], "size_gb": round(result["bytes_changed"] / 1e9, 3),
               "seed": result["seed"], "seconds": seconds, "percent": result["percent"],
               "files_total": result["files_total"], "bytes_total": result["bytes_total"]}
    log_step("Test data changed: %d of %d file(s), %.3f of %.3f GB (%.1f%%) in %s, seed %s (%.1fs)",
             summary["files"], summary["files_total"], summary["total_bytes"] / 1e9, summary["bytes_total"] / 1e9,
             100.0 * summary["total_bytes"] / max(summary["bytes_total"], 1), data_dir, summary["seed"], seconds)
    return summary


def remove_test_data(page, data_dir: str, timeout_ms: int = 1800000) -> dict:
    """Delete the test data folder `data_dir` (the folder itself too) on the
    server of the session `page` shows - only if make_client_shape.py made
    it (its marker file is there), so nothing else is ever deleted.

    Returns {"action": "remove", "folder", "files", "total_bytes",
    "size_gb", "seed", "seconds"}: what was removed (counted on the server
    just before) and how long the delete took. Raises TestDataError if the
    folder is missing, has no marker, or is still there afterwards."""
    data_dir = data_dir.rstrip("/")
    q = shlex.quote(data_dir)
    depth = len([p for p in posixpath.normpath(data_dir).split("/") if p])
    if depth < MIN_CLEAN_DEPTH:
        raise TestDataError("refusing to delete %s, a top-level folder" % data_dir)
    if _shell(page, "if [ -d %s ]; then echo yes; fi" % q, "checking %s" % data_dir).strip() != "yes":
        raise TestDataError("%s does not exist: there is no test data to remove - run the add scenario "
                            "first (test_snapshot_after_adding_data)" % data_dir)
    marker = read_marker(page, data_dir)
    if marker is None:
        raise TestDataError("%s has no %s, so make_client_shape.py did not make it: not deleting it"
                            % (data_dir, MARKER))
    counted = _shell(page, 'printf "%%s %%s" "$(find %s -type f | wc -l)" "$(du -sb %s | cut -f1)"' % (q, q),
                     "measuring %s" % data_dir)
    try:
        files, total_bytes = (int(x) for x in counted.split())
    except ValueError as exc:
        raise TestDataError("cannot read the size of %s: %r" % (data_dir, counted)) from exc
    log_step("Removing the test data: %d file(s), %.2f GB in %s", files, total_bytes / 1e9, data_dir)
    started = time.monotonic()
    _shell(page, "rm -rf %s" % q, "deleting %s" % data_dir, timeout_ms=timeout_ms)
    seconds = round(time.monotonic() - started, 1)
    if _shell(page, "if [ -e %s ]; then echo yes; fi" % q, "checking %s" % data_dir).strip() == "yes":
        raise TestDataError("%s is still there after rm -rf" % data_dir)
    summary = {"action": "remove", "folder": data_dir, "files": files, "total_bytes": total_bytes,
               "size_gb": round(total_bytes / 1e9, 3), "seed": marker.get("seed"), "seconds": seconds}
    log_step("Test data removed: %d file(s), %.2f GB from %s (%.1fs)", files, total_bytes / 1e9, data_dir, seconds)
    return summary
