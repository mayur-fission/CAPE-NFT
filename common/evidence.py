"""Test evidence: screenshots of a tab and copies of files a session wrote,
saved under evidence/ and attached to the running test's Allure report.

Every helper prints its errors instead of raising, so collecting evidence
never fails a test.

Set EVIDENCE_SCREENSHOTS=0 to skip the screenshots (e.g. for very large
runs); failure screenshots are still taken.
"""
import os
import posixpath
import re
import time

from config.perf_report import attach_allure_file

from common.config import EVIDENCE_DIR, env
from common.rstudio_console_commands import read_text_file

SCREENSHOTS_ENABLED = env("EVIDENCE_SCREENSHOTS", "1") not in ("0", "false", "False", "no")
# Larger output files are not copied: they are read back through the
# console, which only keeps its last lines.
OUTPUT_FILE_MAX_BYTES = int(env("EVIDENCE_OUTPUT_FILE_MAX_BYTES", "200000"))


def _evidence_path(name):
    """evidence/<name> with a timestamp before the extension, so a later
    screenshot of the same step and session does not overwrite it."""
    root, ext = os.path.splitext(re.sub(r"[^\w.-]", "_", name))
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
    return os.path.join(EVIDENCE_DIR, "%s_%s%s" % (root, stamp, ext))


def save_screenshot(page, name, always=False):
    """Screenshot `page` to evidence/<name> (timestamped) and attach it to
    Allure as `name`. Skipped when EVIDENCE_SCREENSHOTS=0, unless `always`
    (use it for failures).
    """
    if not (SCREENSHOTS_ENABLED or always):
        return
    path = _evidence_path(name)
    try:
        page.screenshot(path=path)
        print("\n[rstudio-local] screenshot saved to %s" % path)
    except Exception as exc:
        print("\n[rstudio-local] could not save screenshot %s: %s" % (path, exc))
        return
    attach_allure_file(path, name=name)


def save_output_file(page, session_name, path, size=None):
    """Copy the text file `path` (e.g. a script's CSV output) from the
    session to evidence/output_<session name>_<file name> (timestamped) and
    attach it to Allure. Pass its `size` in bytes when known: files over
    EVIDENCE_OUTPUT_FILE_MAX_BYTES are skipped.
    """
    if size is not None and size > OUTPUT_FILE_MAX_BYTES:
        print("\n[rstudio-local] session %s: %s is %d bytes, over EVIDENCE_OUTPUT_FILE_MAX_BYTES (%d) - not copied"
              % (session_name, path, size, OUTPUT_FILE_MAX_BYTES))
        return
    name = "output_%s_%s" % (session_name, posixpath.basename(path))
    local_path = _evidence_path(name)
    try:
        content = read_text_file(page, path)
        if content is None:
            print("\n[rstudio-local] session %s: %s not found, nothing to save" % (session_name, path))
            return
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(local_path, "w", encoding="utf-8", newline="") as f:
            f.write(content.replace("\r\n", "\n").rstrip("\n") + "\n")
        print("\n[rstudio-local] session %s: output saved to %s" % (session_name, local_path))
    except Exception as exc:
        print("\n[rstudio-local] session %s: could not save %s: %s" % (session_name, path, exc))
        return
    attach_allure_file(local_path, name=name)
