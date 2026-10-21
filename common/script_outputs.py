"""A finished script's output file in its session: check it exists (and
save a copy), then delete it; or report it after a timeout. A script that
writes several files gives their paths as a list (check_output_files()).
"""
import posixpath

from common.rstudio_console_commands import get_folder_size
from common.step_log import log_step
from common.rstudio_files import delete_folder
from common.rstudio_console_commands import get_file_size
from common.rstudio_console_commands import get_file_sizes
from common.evidence import save_output_file
from common.rstudio_files import delete_file
from common.rstudio_console_commands import run_console_command
from common.rstudio_console_commands import r_string
from common.rstudio_console_commands import get_console_output
from common.rstudio_console_commands import wait_for_console_ready
from common import locators
from config.config import env

# Tries of each output check; between tries the tab is reloaded. After a long
# run with many tabs a tab's console can stop taking typed commands (e.g. it
# lost its connection to the session), while the R session itself is fine.
OUTPUT_CHECK_ATTEMPTS = int(env("OUTPUT_CHECK_ATTEMPTS", "3"))
_RELOAD_TIMEOUT_MS = int(env("CONSOLE_READY_TIMEOUT_MS", "120000"))


def check_output_file(page, record, output_path, delete=False, remove_dir=None):
    """After a finished run: set record["status"] to an error unless
    `output_path` exists, else copy it to evidence/ and the Allure report
    (see save_output_file()). With `delete`, then delete it, and remove the
    folder `remove_dir` (when given) if that leaves it empty.

    A folder `output_path` (a script writing many files) must hold at least
    one file; it is not copied to evidence/, and `delete` removes it with
    everything in it (also when it is empty). A list of paths is handed to
    check_output_files().
    """
    if isinstance(output_path, (list, tuple)):
        check_output_files(page, record, output_path, delete=delete, remove_dir=remove_dir)
        return
    name = record["session_name"]
    try:
        folder =_read_with_reload(page, name, lambda: get_folder_size(page, output_path))
    except Exception as exc:
        record["status"] = "could not check %s: %s" % (output_path, exc)
        log_step("Output check failed in session %s: %s", name, record["status"])
        return
    if folder is not None:
        count, size = folder
        if count == 0:
            record["status"] = "%s is an empty folder" % output_path
            log_step("Output check failed in session %s: %s", name, record["status"])
        else:
            log_step("Output folder %s verified in session %s: %d file(s), %.1f MB",
                     output_path, name, count, size / 1e6)
        if delete:
            _delete_output(page, record, output_path, delete_folder, remove_dir)
        return
    try:
        size = _read_with_reload(page, name, lambda: get_file_size(page, output_path))
    except Exception as exc:
        record["status"] = "could not check %s: %s" % (output_path, exc)
        log_step("Output check failed in session %s: %s", name, record["status"])
        return
    if size is None:
        record["status"] = "%s was not created" % output_path
        log_step("Output check failed in session %s: %s", name, record["status"])
    else:
        log_step("Output file %s verified in session %s: %d bytes", output_path, name, size)
        save_output_file(page, name, output_path, size=size)
    if delete and size is not None:
        _delete_output(page, record, output_path, delete_file, remove_dir)


def check_output_files(page, record, output_paths, delete=False, remove_dir=None):
    """check_output_file() for a script that writes several files: every one
    of `output_paths` must exist (checked with one console command), else
    record["status"] names the missing ones. The files are copied to
    evidence/ like a single output (save_output_file()). With `delete` and
    all of them there, the files are then deleted, together with the folders
    they are in when those are relative paths ("logs/run_log.txt" removes
    logs/ with anything else the script wrote there), and `remove_dir` if
    that leaves it empty. A failed check deletes nothing.
    """
    name = record["session_name"]
    try:
        sizes = _read_with_reload(page, name, lambda: get_file_sizes(page, output_paths))
    except Exception as exc:
        record["status"] = "could not check %s: %s" % (", ".join(output_paths), exc)
        log_step("Output check failed in session %s: %s", name, record["status"])
        return
    missing = [path for path in output_paths if sizes[path] is None]
    if missing:
        record["status"] = "not created: %s" % ", ".join(missing)
        log_step("Output check failed in session %s: %s", name, record["status"])
    else:
        log_step("Output files verified in session %s: %s", name,
                 ", ".join("%s (%d bytes)" % (path, sizes[path]) for path in output_paths))
    for path in output_paths:
        if sizes[path] is not None:
            save_output_file(page, name, path, size=sizes[path])
    # A failed check keeps whatever the script did write, to look into
    if delete and not missing:
        _delete_outputs(page, record, output_paths, remove_dir)


def _output_folders(output_paths):
    """The top folder of each relative path in `output_paths` ("logs" for
    "logs/run_log.txt"); absolute paths and paths without a folder give
    none."""
    folders = []
    for path in output_paths:
        clean = posixpath.normpath(path)
        if posixpath.isabs(clean) or clean.startswith("..") or "/" not in clean:
            continue
        top = clean.split("/", 1)[0]
        if top not in (".", "~") and top not in folders:
            folders.append(top)
    return folders


def _delete_outputs(page, record, output_paths, remove_dir=None):
    """check_output_files()' clean-up in one console command: delete the
    files and their folders (_output_folders()), then `remove_dir` if that
    left it empty. A failure is only logged, as in _delete_output()."""
    folders = _output_folders(output_paths)
    command = "local({unlink(c(%s)); " % ", ".join(r_string(p) for p in output_paths)
    if folders:
        command += "unlink(c(%s), recursive=TRUE); " % ", ".join(r_string(f) for f in folders)
    if remove_dir:
        command += ("d <- %s; if (length(list.files(d, all.files=TRUE, no..=TRUE)) == 0) unlink(d, recursive=TRUE); "
                    % r_string(remove_dir))
    command += "})"
    try:
        run_console_command(page, command, timeout_ms=30000)
        left = [path for path, size in get_file_sizes(page, output_paths).items() if size is not None]
        if left:
            raise RuntimeError("still there: %s" % ", ".join(left))
        log_step("Output files deleted from session %s", record["session_name"])
    except Exception as exc:
        record["delete_failed"] = str(exc)
        log_step("WARNING: could not delete the output files from session %s (the run still counts as ok; "
                 "delete them by hand): %s", record["session_name"], exc)


def _read_with_reload(page, session_name, read, attempts=OUTPUT_CHECK_ATTEMPTS):
    """read() (a console read on `page`), retried up to `attempts` times;
    before each retry the tab is reloaded - which reconnects it to the
    session without touching R (working directory, files) - and the console
    is waited on until R answers. Raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return read()
        except Exception as exc:
            if attempt == attempts:
                raise
            log_step("Output check in session %s did not get through (attempt %d of %d) - reloading the tab "
                     "and trying again: %s", session_name, attempt, attempts, " ".join(str(exc).split())[:200])
            try:
                page.reload(wait_until="domcontentloaded", timeout=_RELOAD_TIMEOUT_MS)
                wait_for_console_ready(page, timeout_ms=_RELOAD_TIMEOUT_MS)
                log_step("Reloaded the tab of session %s, its console answers again", session_name)
            except Exception as reload_exc:
                log_step("Reloading the tab of session %s failed (trying the check anyway): %s",
                         session_name, " ".join(str(reload_exc).split())[:200])


def _delete_output(page, record, output_path, delete, remove_dir=None):
    """check_output_file()'s clean-up: delete(page, output_path) (a file or
    folder helper), then remove `remove_dir` if that left it empty.

    A failure is only logged as a warning (record["delete_failed"]): the
    script finished and its output was verified before this, so the run
    stays "ok" - deleting is housekeeping, and with many tabs open a busy
    tab can miss the console read it needs.
    """
    try:
        if not delete(page, output_path):
            raise RuntimeError("it is still there")
        if remove_dir:
            run_console_command(
                page,
                "local({d <- %s; if (length(list.files(d, all.files=TRUE, no..=TRUE)) == 0) "
                "unlink(d, recursive=TRUE)})" % r_string(remove_dir),
                timeout_ms=10000,
            )
        log_step("Output %s deleted from session %s", output_path, record["session_name"])
    except Exception as exc:
        record["delete_failed"] = str(exc)
        log_step("WARNING: could not delete output %s from session %s (the run still counts as ok; "
                 "delete it by hand): %s", output_path, record["session_name"], exc)


def report_output_file(page, record, output_path):
    """After a timed-out run: print the console tail and whether
    `output_path` exists yet.
    Interrupts R first (Escape in the console) so it can answer; the file is
    left in place since the script may still have been writing it. A list
    of paths reports each of them.
    """
    try:
        console = get_console_output(page)
        print("\n[rstudio-local] session %s: console at the timeout (last 40 lines):\n%s"
              % (record["session_name"], "\n".join(console.splitlines()[-40:])))
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not read the console: %s" % (record["session_name"], exc))
    try:
        page.locator(locators.CONSOLE_INPUT_SELECTOR).first.click(force=True)
        page.keyboard.press("Escape")
        page.wait_for_timeout(2000)
        if isinstance(output_path, (list, tuple)):
            sizes = get_file_sizes(page, output_path)
        else:
            size = get_file_size(page, output_path)
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not check %s: %s" % (record["session_name"], output_path, exc))
        return
    if isinstance(output_path, (list, tuple)):
        print("\n[rstudio-local] session %s: output at the timeout (not deleted): %s"
              % (record["session_name"], ", ".join("%s %s" % (p, "missing" if s is None else "%d bytes" % s)
                                                  for p, s in sizes.items())))
        return
    if size is None:
        print("\n[rstudio-local] session %s: %s was not created before the timeout"
              % (record["session_name"], output_path))
    else:
        print("\n[rstudio-local] session %s: %s exists at the timeout, %d bytes (not deleted)"
              % (record["session_name"], output_path, size))
