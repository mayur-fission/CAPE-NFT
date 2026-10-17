"""A finished script's output file in its session: check it exists (and
save a copy), then delete it; or report it after a timeout.
"""
from common.rstudio_console_commands import get_folder_size
from common.step_log import log_step
from common.rstudio_files import delete_folder
from common.rstudio_console_commands import get_file_size
from common.evidence import save_output_file
from common.rstudio_files import delete_file
from common.rstudio_console_commands import run_console_command
from common.rstudio_console_commands import r_string
from common.rstudio_console_commands import get_console_output
from common import locators


def check_output_file(page, record, output_path, delete=False, remove_dir=None):
    """After a finished run: set record["status"] to an error unless
    `output_path` exists, else copy it to evidence/ and the Allure report
    (see save_output_file()). With `delete`, then delete it, and remove the
    folder `remove_dir` (when given) if that leaves it empty.

    A folder `output_path` (a script writing many files) must hold at least
    one file; it is not copied to evidence/, and `delete` removes it with
    everything in it (also when it is empty).
    """
    name = record["session_name"]
    try:
        folder = get_folder_size(page, output_path)
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
        size = get_file_size(page, output_path)
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
    left in place since the script may still have been writing it.
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
        size = get_file_size(page, output_path)
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not check %s: %s" % (record["session_name"], output_path, exc))
        return
    if size is None:
        print("\n[rstudio-local] session %s: %s was not created before the timeout"
              % (record["session_name"], output_path))
    else:
        print("\n[rstudio-local] session %s: %s exists at the timeout, %d bytes (not deleted)"
              % (record["session_name"], output_path, size))
