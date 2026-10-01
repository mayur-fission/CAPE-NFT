"""R console automation: running commands and inspecting the session's
filesystem.

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


def _type_command(page, command):
    page.locator(locators.CONSOLE_INPUT_SELECTOR).first.click(force=True)
    page.keyboard.type(command)
    page.keyboard.press("Enter")


def read_console_value(page, r_expr, timeout_ms=10000):
    """Evaluate `r_expr` in the console and return its value as a str.

    `r_expr` must produce one value cat() can print. Raises RuntimeError if
    nothing is printed within timeout_ms (including when `r_expr` errors).
    """
    marker = _unique_marker("VAL")
    _type_command(page, "cat(%s, '=', %s, '\\n', sep='')" % (_r_split_literal(marker), r_expr))

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


def get_file_size(page, path, timeout_ms=10000):
    """Size of `path` in bytes, or None if it doesn't exist."""
    value = read_console_value(page, 'format(file.size(%s), scientific=FALSE)' % r_string(path), timeout_ms)
    return None if value == "NA" else int(value)


def get_folder_size(page, path, recursive=True, timeout_ms=15000):
    """Total size in bytes of the files under `path` (0 if empty). Unreadable
    entries are skipped.
    """
    r_expr = (
        'format(sum(file.info(list.files(%s, full.names=TRUE, recursive=%s))$size, na.rm=TRUE), '
        "scientific=FALSE)" % (r_string(path), "TRUE" if recursive else "FALSE")
    )
    return int(read_console_value(page, r_expr, timeout_ms))


def get_file_count(page, path, recursive=False, timeout_ms=10000):
    """Number of files in `path` (0 if empty or missing). recursive=True
    includes subdirectories.
    """
    r_expr = 'format(length(list.files(%s, recursive=%s)), scientific=FALSE)' % (
        r_string(path), "TRUE" if recursive else "FALSE"
    )
    return int(read_console_value(page, r_expr, timeout_ms))


def check_file_exists(page, path, timeout_ms=10000):
    """Whether `path` exists on the session's filesystem."""
    return read_console_value(page, 'format(file.exists(%s))' % r_string(path), timeout_ms) == "TRUE"


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


def get_file_content(page, path, timeout_ms=10000):
    """Text content of `path`, lines joined with "\n" (a trailing newline is
    not preserved). Raises RuntimeError on timeout or if `path` doesn't exist.
    """
    r_statements = "cat(paste(readLines(%s), collapse='\\n'))" % r_string(path)
    return _read_console_block(page, r_statements, timeout_ms, "file content for %r" % path)


def verify_file_content(page, path, expected_content, timeout_ms=10000):
    """Assert that `path` contains `expected_content` and return it."""
    actual_content = get_file_content(page, path, timeout_ms=timeout_ms)
    assert actual_content == expected_content, (
        "file content is %r, expected %r" % (actual_content, expected_content)
    )
    return actual_content


def submit_console_command(page, command):
    """Type `command` into the console and press Enter without waiting.

    Returns (marker, started) for is_console_command_done() or
    wait_for_console_command().
    """
    marker = _unique_marker("DONE")
    full_command = "%s; cat(%s, '\\n', sep='')" % (command, _r_split_literal(marker))

    page.locator(locators.CONSOLE_INPUT_SELECTOR).first.click(force=True)
    page.keyboard.type(full_command)

    started = time.time()
    page.keyboard.press("Enter")
    return marker, started


def is_console_command_done(page, marker):
    """Non-blocking check for whether `marker` has appeared in the console."""
    return marker in page.locator(locators.CONSOLE_OUTPUT_SELECTOR).inner_text()


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


def run_r_script(page, script_path, timeout_ms=300000, poll_interval_ms=500):
    """source() `script_path` in the console and return the elapsed seconds.
    Raises RuntimeError on timeout (including if the script errors).
    """
    return run_console_command(
        page, 'source(%s)' % r_string(script_path), timeout_ms=timeout_ms, poll_interval_ms=poll_interval_ms
    )
