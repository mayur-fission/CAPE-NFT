"""One readable line per test step, printed and sent to the "cape_nft"
logger (pytest log section, Allure). Lines from a browser thread of
common.parallel_browsers are tagged with that browser:

    [rstudio-local] 15:53:17 | browser B1 | R script htp_10min.R started execution in session CONCURRENT_10MIN_SESSION_1

Under pytest every line is also appended, as it happens, to step_log.txt in
the running test file's evidence folder (config.config evidence_dir()), so
a run that hangs or is stopped still leaves its log there.
"""
import logging
import os
import threading
import time

_logger = logging.getLogger("cape_nft")
STEP_LOG_FILE = "step_log.txt"
_file_lock = threading.Lock()


def current_browser():
    """'B1' when called from a browser thread (see run_in_parallel_browsers()),
    else None."""
    name = threading.current_thread().name
    return name[len("browser-"):] if name.startswith("browser-") else None


def _write_to_log_file(line):
    """Append `line` to the running test file's step_log.txt (pytest only).
    Never raises: logging must not fail a test."""
    try:
        from config.config import current_test_file, evidence_dir

        if not current_test_file():
            return
        path = os.path.join(evidence_dir(), STEP_LOG_FILE)
        with _file_lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def log_step(message, *args):
    """Print and log one step: `message` % `args`, with the time and, from a
    browser thread, the browser."""
    text = message % args if args else message
    browser = current_browser()
    line = "%s | %s%s" % (time.strftime("%H:%M:%S"), "browser %s | " % browser if browser else "", text)
    print("\n[rstudio-local] %s" % line, flush=True)
    _logger.info(line)
    _write_to_log_file(line)
