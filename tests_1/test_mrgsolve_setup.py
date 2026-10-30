"""Setup for test_mrgsolve_concurrent.py: check the R packages the mrgsolve
script needs (MRGSOLVE_R_PACKAGES, default mrgsolve) and install any missing
ones into the user's library, from a setup session of its own
(MRGSOLVE_SESSION_SETUP), quit again at the end. Run it once per user and
server before the concurrent test, so the timed runs never include an
install.

Flow: login -> quit a leftover MRGSOLVE_SESSION_SETUP -> launch it -> check
      the packages, install the missing ones -> quit it
"""
import pytest

from common.r_packages import MGSOLVE_PACKAGES as MRGSOLVE_PACKAGES, install_missing_r_packages
from common.session_naming_logic import MRGSOLVE_SESSION_PREFIX
from common.step_log import log_step
from config.config import env, env_list

pytestmark = pytest.mark.rstudio_local

USER = int(env("CONCURRENT_SESSIONS_USER", "1"))
# MRGSOLVE_R_PACKAGES (comma-separated) overrides the default ("none" = no check).
R_PACKAGES = [p for p in env_list("MRGSOLVE_R_PACKAGES", MRGSOLVE_PACKAGES) if p.lower() != "none"]
SETUP_SESSION_NAME = MRGSOLVE_SESSION_PREFIX + "SETUP"


def test_mrgsolve_setup(browser_name, browser_type_launch_args, browser_context_args):
    """Every package in MRGSOLVE_R_PACKAGES is installed for the user
    (installing the missing ones) - see the module docstring."""
    if not R_PACKAGES:
        pytest.skip("MRGSOLVE_R_PACKAGES is none - no R packages to check")
    installed = install_missing_r_packages(browser_name, browser_type_launch_args, browser_context_args, USER,
                                           R_PACKAGES, SETUP_SESSION_NAME)
    log_step("R packages for the mrgsolve script ready (%s)%s", ", ".join(R_PACKAGES),
             "; installed: %s" % ", ".join(installed) if installed else "; all were installed already")
