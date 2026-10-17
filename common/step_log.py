"""One readable line per test step, printed and sent to the "cape_nft"
logger (pytest log section, Allure). Lines from a browser thread of
common.parallel_browsers are tagged with that browser:

    [rstudio-local] 15:53:17 | browser B1 | R script htp_10min.R started execution in session CONCURRENT_10MIN_SESSION_1
"""
import logging
import threading
import time

_logger = logging.getLogger("cape_nft")


def current_browser():
    """'B1' when called from a browser thread (see run_in_parallel_browsers()),
    else None."""
    name = threading.current_thread().name
    return name[len("browser-"):] if name.startswith("browser-") else None


def log_step(message, *args):
    """Print and log one step: `message` % `args`, with the time and, from a
    browser thread, the browser."""
    text = message % args if args else message
    browser = current_browser()
    line = "%s | %s%s" % (time.strftime("%H:%M:%S"), "browser %s | " % browser if browser else "", text)
    print("\n[rstudio-local] %s" % line, flush=True)
    _logger.info(line)
