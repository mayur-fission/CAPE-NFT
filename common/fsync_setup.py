"""Per-session setup for test_fsync_concurrent.py, run in each session's
own folder (already its working directory) before the R scripts start:

  1. shell checks, through R system2() on the session's server:
       cd <base> && pwd
       which python3 && python3 --version
       df -h <base>
  2. a Python virtual env in the session folder:
       cd <session folder> && python3 -m venv <session folder>/venv
       <session folder>/venv/bin/python --version
  3. put the Python and R files into the session folder, each checked by
     its md5:
       mode "copy"   - files already on the server, copied there (as the
                       Files pane's More -> Copy To... does)
       mode "upload" - local files, with the Files pane's Upload button
                       (pane on the folder -> Upload -> file -> OK)
  4. setwd to the session folder from the Files pane (More -> Set As
     Working Directory; console setwd() if that fails), then Sys.setenv()
     FSYNC_SESSION_DIR / FSYNC_VENV_PYTHON / FSYNC_PY_FILE, so
     the R script can find the venv's python and the uploaded .py file

Each step's output and time go into a setup record per session (see
write_setup_csv()). A failed step stops that session's setup only.
"""
import hashlib
import os
import posixpath
import shlex
import time

from common import locators
from common.evidence import save_screenshot
from common.files_pane import open_folder_in_files_pane
from common.perf_report import write_csv_report
from common.rstudio_console_commands import r_string, read_console_value, run_console_command
from common.step_log import log_step

# Joins a command's output lines in the one-line value read from the console
_LINE_SEPARATOR = " | "
# Between a command's exit status and its output in that value (a tab may
# not survive the console's text)
_STATUS_SEPARATOR = "@@EXIT@@"
# How long to look for a Files pane button/menu item before falling back
_UI_TIMEOUT_MS = 10000


class SessionSetupError(RuntimeError):
    """A setup step failed; the session's run record shows "setup failed: ..."."""
    status_prefix = "setup failed"


def run_shell(page, command, timeout_ms=300000):
    """Run `command` with bash on the session's server (R system2(), output
    and errors together). Returns (exit status, output lines joined by
    " | ")."""
    r_expr = (
        "{out <- suppressWarnings(system2('/bin/bash', c('-c', shQuote(%s)), stdout = TRUE, stderr = TRUE)); "
        "st <- attr(out, 'status'); paste0(if (is.null(st)) 0L else st, %s, "
        "paste(gsub('[[:cntrl:]]', ' ', out), collapse = %s))}"
        % (r_string(command), r_string(_STATUS_SEPARATOR), r_string(_LINE_SEPARATOR))
    )
    value = read_console_value(page, r_expr, timeout_ms)
    status, sep, output = value.partition(_STATUS_SEPARATOR)
    if not sep or not status.strip().lstrip("-").isdigit():
        raise SessionSetupError("could not read the result of $ %s: %r" % (command, value[:200]))
    return int(status), output.strip()


def upload_file(page, local_path, folder, timeout_ms=120000):
    """Upload `local_path` into the server folder `folder` through the Files
    pane's Upload dialog, and check on the server that it arrived whole (md5).
    Returns the uploaded file's server path. Raises SessionSetupError if not.

    Note: RStudio unpacks an uploaded .zip into the folder.
    """
    name = os.path.basename(local_path)
    remote_path = posixpath.join(folder, name)
    open_folder_in_files_pane(page, folder)
    panel = page.locator(locators.FILES_PANEL_SELECTOR)
    upload = page.locator(locators.FILES_UPLOAD_BUTTON_SELECTOR)
    if upload.count() == 0:
        upload = panel.get_by_role("button", name=locators.FILES_UPLOAD_BUTTON)
    upload.first.click(timeout=timeout_ms)

    # "Upload Files": Target directory [text] [Browse...], File to upload
    # [Choose file], OK / Cancel
    dialog = page.locator(locators.DIALOG_SELECTOR).filter(
        has=page.locator(locators.FILES_UPLOAD_INPUT_SELECTOR)).last
    try:
        dialog.wait_for(state="visible", timeout=_UI_TIMEOUT_MS)
    except Exception:
        # the dialog box without a dialog role: find it by its title
        dialog = page.locator(locators.FILES_UPLOAD_DIALOG_BOX_SELECTOR).filter(
            has_text=locators.FILES_UPLOAD_DIALOG_TITLE).last
        dialog.wait_for(state="visible", timeout=timeout_ms)
    # The target directory is the folder the Files pane shows, which may not
    # have caught up yet: make sure it is the session folder
    target = dialog.locator(locators.FILES_UPLOAD_TARGET_DIR_SELECTOR).first
    if target.count() > 0:
        current = (target.input_value(timeout=_UI_TIMEOUT_MS) or "").strip()
        if current.rstrip("/") != folder.rstrip("/"):
            log_step("Upload dialog target directory is %s, setting it to %s", current, folder)
            try:
                target.fill(folder, timeout=_UI_TIMEOUT_MS)
            except Exception as exc:
                page.keyboard.press("Escape")
                raise SessionSetupError("upload dialog target directory is %s, not %s, and cannot be changed: %s"
                                        % (current, folder, " ".join(str(exc).split())[:150]))
    # "Choose file" is a native file input: set the file on it directly, no
    # OS file chooser
    dialog.locator(locators.FILES_UPLOAD_INPUT_SELECTOR).first.set_input_files(local_path, timeout=timeout_ms)
    save_screenshot(page, "fsync_upload_%s.png" % name)
    ok = page.locator(locators.FILES_UPLOAD_OK_SELECTOR)
    if ok.count() == 0:
        ok = dialog.get_by_role("button", name=locators.FILES_UPLOAD_OK_BUTTON, exact=True)
    ok.first.click(timeout=timeout_ms)

    with open(local_path, "rb") as f:
        expected = hashlib.md5(f.read()).hexdigest()
    r_path = r_string(remote_path)
    deadline = time.time() + timeout_ms / 1000.0
    actual = ""
    while time.time() < deadline:
        actual = read_console_value(page, "if (file.exists(%s)) unname(tools::md5sum(%s)) else ''"
                                    % (r_path, r_path))
        if actual == expected:
            return remote_path
        page.wait_for_timeout(2000)
    save_screenshot(page, "fsync_upload_failed_%s.png" % name, always=True)
    raise SessionSetupError("upload of %s to %s: %s" % (
        name, folder, "file not there" if not actual else "md5 %s, expected %s" % (actual, expected)))


def copy_file(page, server_path, folder, timeout_ms=120000):
    """Copy the server file `server_path` into the server folder `folder`
    (R file.copy(), as Files pane > More > Copy To... does) and check its
    md5 against the source. Returns the copy's path; raises
    SessionSetupError if it failed."""
    target = posixpath.join(folder, posixpath.basename(server_path))
    src, dst = r_string(server_path), r_string(target)
    value = read_console_value(page, (
        "if (!file.exists(%s)) 'missing' else if (!file.copy(%s, %s, overwrite = TRUE)) 'copy failed' "
        "else if (unname(tools::md5sum(%s)) != unname(tools::md5sum(%s))) 'md5 differs' else 'ok'")
        % (src, src, dst, src, dst), timeout_ms)
    if value != "ok":
        raise SessionSetupError("copy of %s to %s: %s" % (
            server_path, folder, "source file not found" if value == "missing" else value))
    return target


def set_working_directory_from_files_pane(page, session_name, folder, timeout_ms=30000):
    """setwd to `folder` the way a user does: Files pane on the folder ->
    More -> Set As Working Directory, then check getwd(). Falls back to
    setwd() in the console if the menu did not do it; raises
    SessionSetupError if neither did."""
    try:
        open_folder_in_files_pane(page, folder)
        panel = page.locator(locators.FILES_PANEL_SELECTOR)
        panel.get_by_role("button", name=locators.FILES_MORE_BUTTON).first.click(timeout=_UI_TIMEOUT_MS)
        item = page.get_by_text(locators.FILES_SET_AS_WORKING_DIR_ITEM, exact=True).last
        item.click(timeout=_UI_TIMEOUT_MS)
        page.wait_for_timeout(1500)
    except Exception as exc:
        log_step("Session %s: Files pane More -> Set As Working Directory failed (%s), using setwd()",
                 session_name, " ".join(str(exc).split())[:200])
        page.keyboard.press("Escape")  # close the menu if it is still open
    if read_console_value(page, "normalizePath(getwd())") != read_console_value(
            page, "normalizePath(%s)" % r_string(folder)):
        run_console_command(page, "setwd(%s)" % r_string(folder), timeout_ms=timeout_ms)
    actual = read_console_value(page, "getwd()")
    if actual.rstrip("/") != folder.rstrip("/"):
        raise SessionSetupError("working directory is %s, expected %s" % (actual, folder))
    log_step("Session %s: working directory set to %s (Files pane > More > Set As Working Directory)",
             session_name, folder)


def setup_session(page, session_name, session_dir, base, files, mode="copy", python="python3",
                  timeout_ms=600000):
    """Steps 1-4 of the module docstring for one session; `files` are server
    paths with mode "copy", local paths with "upload". Returns its setup
    record: {"session_name", "session_dir", "status" ("ok" or why not),
    "setup_s", "upload_s", "python", "venv_python", "df", "files"}.
    Raises nothing: a failure is in "status" (and record["error"])."""
    record = {"session_name": session_name, "session_dir": session_dir, "status": "not run",
              "setup_s": None, "upload_s": None, "python": "", "venv_python": "", "df": "", "files": ""}
    q_base, q_dir, q_py = shlex.quote(base), shlex.quote(session_dir), shlex.quote(python)
    venv_python = posixpath.join(session_dir, "venv", "bin", "python")
    steps = [
        ("cd base", "cd %s && pwd" % q_base, None),
        ("python3", "which %s && %s --version" % (q_py, q_py), "python"),
        ("disk space", "df -h %s" % q_base, "df"),
        ("create venv", "cd %s && %s -m venv %s" % (q_dir, q_py, shlex.quote(posixpath.join(session_dir, "venv"))),
         None),
        ("venv python", "%s --version" % shlex.quote(venv_python), "venv_python"),
    ]
    try:
        started = time.time()
        for what, command, key in steps:
            step_started = time.time()
            status, output = run_shell(page, command, timeout_ms)
            log_step("Session %s setup - %s (%.1fs, exit %d): $ %s -> %s", session_name, what,
                     time.time() - step_started, status, command, output)
            if status != 0:
                raise SessionSetupError("%s: exit %d: %s" % (what, status, output[:300]))
            if key:
                record[key] = output
        record["setup_s"] = round(time.time() - started, 2)

        started = time.time()
        uploaded = []
        for path in files:
            if mode == "upload":
                uploaded.append(upload_file(page, path, session_dir))
            else:
                uploaded.append(copy_file(page, path, session_dir))
            log_step("Session %s: %s %s to %s", session_name, "uploaded" if mode == "upload" else "copied",
                     path, session_dir)
        record["upload_s"] = round(time.time() - started, 2)
        record["files"] = ", ".join(posixpath.basename(p) for p in uploaded)
        set_working_directory_from_files_pane(page, session_name, session_dir)
        py_files = [p for p in uploaded if p.endswith(".py")]
        run_console_command(page, "Sys.setenv(FSYNC_SESSION_DIR = %s, FSYNC_VENV_PYTHON = %s, FSYNC_PY_FILE = %s)"
                            % (r_string(session_dir), r_string(venv_python),
                               r_string(py_files[0] if py_files else "")), timeout_ms=60000)
        record["status"] = "ok"
    except Exception as exc:
        record["error"] = exc if isinstance(exc, SessionSetupError) else SessionSetupError(
            " ".join(str(exc).split())[:300])
        record["status"] = "setup failed: %s" % record["error"]
        log_step("Session %s: setup failed: %s", session_name, record["error"])
        save_screenshot(page, "fsync_setup_failed_%s.png" % session_name, always=True)
    return record


def write_setup_csv(path, records):
    """One row per session setup record, in session order."""
    rows = [["Session Name", "Session Folder", "Setup Status", "Setup Time (s)", "Copy/Upload Time (s)",
             "python3", "venv python", "Files", "df -h"]]
    for r in records:
        rows.append([r["session_name"], r["session_dir"] or "", r["status"],
                     "" if r["setup_s"] is None else "%.2f" % r["setup_s"],
                     "" if r["upload_s"] is None else "%.2f" % r["upload_s"],
                     r["python"], r["venv_python"], r["files"], r["df"]])
    write_csv_report(path, rows)
    print("[rstudio-local] wrote session setup results to %s" % path)
