"""R console automation: run commands (blocking or not), read values back
and inspect the session's filesystem.

Results are read back from the console output text, tagged with a unique
marker that R assembles via paste0() - so the marker only appears once R
prints it, never in the echoed command.
"""
import itertools
import re
import time

from common import locators

_POLL_INTERVAL_MS = 200

_marker_seq = itertools.count()


def _unique_marker(kind):
    # Timestamp last, so no marker is a prefix of another (matched by substring).
    return "AUTO_PERF_%s_%d_%d" % (kind, next(_marker_seq), int(time.time() * 1000))


def _r_split_literal(marker):
    """R expression that evaluates to `marker` without spelling it out."""
    half = len(marker) // 2
    return "paste0('%s', '%s')" % (marker[:half], marker[half:])


def r_string(value):
    """`value` as an escaped, double-quoted R string literal."""
    escaped = (
        str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    return '"%s"' % escaped


def _type_command(page, command, marker=None, attempts=3, echo_timeout_ms=10000):
    """Type `command` into the console input and press Enter.

    With `marker` (split into `command` by _r_split_literal()), also wait
    until the console shows the command was taken: its echo or its output
    contains the marker's second half. Right after a session loads the IDE
    can steal focus and drop the keystrokes; then the input is empty and
    the command is typed again, or if it is still sitting in the input,
    Enter is pressed again. Raises RuntimeError if it is never taken.
    """
    console_input = page.locator(locators.CONSOLE_INPUT_SELECTOR).first
    console_input.click(force=True)
    page.keyboard.type(command)
    page.keyboard.press("Enter")
    if marker is None:
        return

    fragment = marker[len(marker) // 2:]
    for attempt in range(attempts):
        deadline = time.time() + echo_timeout_ms / 1000.0
        while time.time() < deadline:
            if fragment in get_console_output(page):
                return
            page.wait_for_timeout(_POLL_INTERVAL_MS)
        if attempt + 1 == attempts:
            break
        console_input.click(force=True)
        typed = " ".join(page.locator(locators.CONSOLE_INPUT_TEXT_SELECTOR).first.inner_text().split())
        if typed == " ".join(command.split()):
            print("[rstudio-local] console command not submitted, pressing Enter again")
        else:
            print("[rstudio-local] console command was lost, typing it again")
            if typed:  # only part of it arrived
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            page.keyboard.type(command)
        page.keyboard.press("Enter")
    raise RuntimeError("the console did not take the command %r" % command[:120])


def read_console_value(page, r_expr, timeout_ms=10000):
    """Evaluate `r_expr` in the console and return its value as a str.

    `r_expr` must produce one value cat() can print. Raises RuntimeError if
    nothing is printed within timeout_ms (including when `r_expr` errors).
    """
    marker = _unique_marker("VAL")
    _type_command(page, "cat(%s, '=', %s, '\\n', sep='')" % (_r_split_literal(marker), r_expr), marker=marker)

    pattern = re.compile(re.escape(marker) + r"=(.*)")
    console = page.locator(locators.CONSOLE_OUTPUT_SELECTOR)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        match = pattern.search(console.inner_text())
        if match:
            return match.group(1).strip()
        page.wait_for_timeout(_POLL_INTERVAL_MS)

    raise RuntimeError(
        "console printed no result for %s within %dms (the R expression may have errored)"
        % (r_expr, timeout_ms)
    )


def _read_console_block(page, r_statements, timeout_ms, what):
    """Run `r_statements` between start/end markers and return the text they
    print. Raises RuntimeError on timeout.
    """
    start, end = _unique_marker("START"), _unique_marker("END")
    _type_command(
        page,
        "cat(%s, '\\n', sep=''); %s; cat('\\n', %s, '\\n', sep='')"
        % (_r_split_literal(start), r_statements, _r_split_literal(end)),
        marker=start,
    )

    # Only horizontal whitespace is tolerated around the marker lines - a
    # bare \s* would also swallow blank lines at the start/end of the content.
    pattern = re.compile(re.escape(start) + r"[ \t\r]*\n(.*?)\n[ \t\r]*" + re.escape(end), re.S)
    console = page.locator(locators.CONSOLE_OUTPUT_SELECTOR)
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        match = pattern.search(console.inner_text())
        if match:
            return match.group(1)
        page.wait_for_timeout(_POLL_INTERVAL_MS)

    raise RuntimeError(
        "%s did not appear in the console within %dms (the R command may have errored)"
        % (what, timeout_ms)
    )


def r_source_command(script_path):
    """R command that source()s `script_path`."""
    return "source(%s)" % r_string(script_path)


def get_file_size(page, path, timeout_ms=10000):
    """Size of `path` in bytes, or None if it doesn't exist."""
    value = read_console_value(page, 'format(file.size(%s), scientific=FALSE)' % r_string(path), timeout_ms)
    return None if value == "NA" else int(value)


def read_text_file(page, path, timeout_ms=30000):
    """Contents of the text file `path` (read by printing it in the console),
    or None if it doesn't exist. Meant for small files: the console only
    keeps its last lines.
    """
    r_statements = "local({p <- %s; if (file.exists(p)) cat(readLines(p, warn=FALSE), sep='\\n') " \
                   "else cat('%s')})" % (r_string(path), _MISSING_FILE)
    body = _read_console_block(page, r_statements, timeout_ms, "contents of %r" % path)
    return None if body.strip() == _MISSING_FILE else body


_MISSING_FILE = "@@AUTO_PERF_MISSING@@"
_FILE_LIST_SEPARATOR = "@@AUTO_PERF_SEP@@"


def get_file_list(page, path, recursive=False, timeout_ms=15000):
    """Names of the files in `path` ([] if empty or missing). recursive=True
    includes subdirectories.
    """
    r_statements = "cat(paste(list.files(%s, recursive=%s), collapse='%s'))" % (
        r_string(path), "TRUE" if recursive else "FALSE", _FILE_LIST_SEPARATOR
    )
    body = _read_console_block(page, r_statements, timeout_ms, "file list for %r" % path).strip()
    return body.split(_FILE_LIST_SEPARATOR) if body else []


def submit_console_command(page, command, confirm=True):
    """Type `command` into the console and press Enter without waiting for
    it to finish. With confirm (see _type_command()), retypes it if the
    keystrokes were lost.

    Returns (marker, started) for is_console_command_done() or
    wait_for_console_command(); started is when the command was taken.
    """
    marker = _unique_marker("DONE")
    full_command = "%s; cat(%s, '\\n', sep='')" % (command, _r_split_literal(marker))
    _type_command(page, full_command, marker=marker if confirm else None)
    return marker, time.time()


def is_console_command_done(page, marker):
    """Non-blocking check for whether `marker` has appeared in the console."""
    return marker in get_console_output(page)


def wait_for_console_command(page, marker, started, timeout_ms=300000, poll_interval_ms=500):
    """Wait until `marker` appears and return seconds since `started`.

    An R error stops the marker from printing, so errors surface as a
    RuntimeError timeout rather than a false success.
    """
    deadline = started + timeout_ms / 1000.0
    while time.time() < deadline:
        if is_console_command_done(page, marker):
            return time.time() - started
        page.wait_for_timeout(poll_interval_ms)

    raise RuntimeError(
        "command did not complete within %dms - the marker %r never "
        "appeared in the console (it may have errored, or is still running)"
        % (timeout_ms, marker)
    )


def wait_for_console_ready(page, timeout_ms=120000, probe_interval_ms=15000, poll_interval_ms=500):
    """Wait until the console has loaded and R answers commands, and return
    the seconds waited.

    Waits for the console input, then sends a no-op probe and waits for R
    to print its marker - which only happens once R is idle, e.g. after a
    resumed session has restored its workspace. The probe is re-sent every
    probe_interval_ms in case its keystrokes were lost while the console
    was still starting up. Raises RuntimeError on timeout.
    """
    started = time.time()
    deadline = started + timeout_ms / 1000.0
    page.locator(locators.CONSOLE_INPUT_SELECTOR).first.wait_for(state="visible", timeout=timeout_ms)

    markers = []
    while time.time() < deadline:
        # Not confirmed: this loop re-sends the probe itself.
        markers.append(submit_console_command(page, "invisible(NULL)", confirm=False)[0])
        probe_deadline = min(deadline, time.time() + probe_interval_ms / 1000.0)
        while time.time() < probe_deadline:
            output = page.locator(locators.CONSOLE_OUTPUT_SELECTOR).inner_text()
            if any(marker in output for marker in markers):
                return time.time() - started
            page.wait_for_timeout(poll_interval_ms)

    raise RuntimeError("console did not become ready within %dms" % timeout_ms)


def run_console_command(page, command, timeout_ms=300000, poll_interval_ms=500):
    """Run `command` in the console, wait for it to finish, and return the
    elapsed seconds.

    A split marker is appended and polled for: RStudio echoes the submitted
    line immediately, so a literal marker would match before the command
    finishes. Raises RuntimeError on timeout.
    """
    marker, started = submit_console_command(page, command)
    return wait_for_console_command(
        page, marker, started, timeout_ms=timeout_ms, poll_interval_ms=poll_interval_ms
    )


def get_console_output(page):
    """Full text currently in the console output pane."""
    return page.locator(locators.CONSOLE_OUTPUT_SELECTOR).inner_text()
