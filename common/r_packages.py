"""R packages a test script needs: check which are installed on the server
and install the missing ones, from a setup session of their own, before the
test's sessions source the script. The packages go into the user's library
(R_LIBS_USER), which every session of that user then sees, so they are
installed once, not in every session, and the install time is not part of
the script timings.
"""
import time

from common.parallel_browsers import run_in_parallel_browsers
from common.rstudio_console_commands import r_string, read_console_value, run_console_command
from common.rstudio_session_helper import launch_new_session
from common.session_actions import login_to_posit_workbench
from common.session_cleanup import quit_sessions_named
from common.step_log import log_step
from config.config import env

# A package install (with its dependencies) can take a while, more so when
# the repository has no binaries and they are compiled.
INSTALL_TIMEOUT_MS = int(env("R_PACKAGES_INSTALL_TIMEOUT_MS", "3600000"))
# Repository to install from when R has none set (getOption("repos") is
# "@CRAN@", which would open RStudio's mirror chooser).
FALLBACK_REPO = env("R_PACKAGES_REPO", "https://cloud.r-project.org")
SETUP_LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))

# The R packages each 10-minute workload script needs (test_<name>_10min and
# test_pmt_10min); each test's <NAME>_R_PACKAGES setting overrides its list.
MGSOLVE_PACKAGES = ["mrgsolve"]
PKNCA_PACKAGES = ["PKNCA", "tidyr"]
NLMIXR_PACKAGES = ["nlmixr2", "rxode2"]
SHINY_HTML_PACKAGES = ["rmarkdown", "knitr", "shiny", "callr", "httpuv"]
TIDYVERSE_GGPLOT_PACKAGES = ["tidyverse"]

# (The console commands below have no braces: RStudio's console adds a
# closing one as each opening one is typed.)
_LIB = 'if (nzchar(Sys.getenv("R_LIBS_USER"))) Sys.getenv("R_LIBS_USER") else .libPaths()[1]'


def _r_vector(packages):
    return "c(%s)" % ", ".join(r_string(p) for p in packages)


def missing_r_packages(page, packages):
    """The ones of `packages` not installed in any library of the session
    on `page`."""
    value = read_console_value(
        page, "paste(Filter(function(p) !nzchar(system.file(package=p)), %s), collapse=',')" % _r_vector(packages),
        timeout_ms=30000,
    )
    return [p for p in value.split(",") if p]


def ensure_r_packages(page, packages, timeout_ms=INSTALL_TIMEOUT_MS):
    """Install the missing ones of `packages` (with the packages they depend
    on) into the user's library, in the session on `page`. Returns the ones
    it installed; raises RuntimeError if any are still missing afterwards."""
    missing = missing_r_packages(page, packages)
    if not missing:
        log_step("R packages already installed: %s", ", ".join(packages))
        return []
    log_step("R packages not installed: %s - installing them (with their dependencies)", ", ".join(missing))
    # Timed here, not by run_console_command(): its clock starts only once
    # the console has taken the command, which can be well after it is typed
    started = time.time()
    run_console_command(
        page,
        "lib <- %s; dir.create(lib, recursive=TRUE, showWarnings=FALSE); .libPaths(c(lib, .libPaths())); "
        "repos <- getOption('repos'); if (any(repos == '@CRAN@')) repos <- %s; "
        "install.packages(%s, lib=lib, repos=repos, Ncpus=max(1L, parallel::detectCores() - 1L))"
        % (_LIB, r_string(FALLBACK_REPO), _r_vector(missing)),
        timeout_ms=timeout_ms, poll_interval_ms=5000,
    )
    still = missing_r_packages(page, missing)
    if still:
        raise RuntimeError("R packages still not installed after install.packages() (see its output in the "
                           "console screenshot): %s" % ", ".join(still))
    log_step("Installed R packages %s in %.0fs", ", ".join(missing), time.time() - started)
    return missing


def install_missing_r_packages(browser_name, launch_args, context_args, user, packages, session_name):
    """ensure_r_packages() from a session of its own, `session_name`, in a
    browser of its own: login -> quit a leftover `session_name` -> launch it
    -> check and install `packages` -> quit it. Returns the packages it
    installed; raises if the check or the install failed."""
    if not packages:
        return []

    def _worker(context, prefix):
        page = context.new_page()
        home_url = login_to_posit_workbench(page, user)
        quit_sessions_named(page, home_url, [session_name])
        try:
            launch_new_session(page, home_url, session_name, timeout_ms=SETUP_LAUNCH_TIMEOUT_MS)
            return ensure_r_packages(page, packages)
        finally:
            try:
                quit_sessions_named(page, home_url, [session_name])
            except Exception as exc:
                log_step("WARNING: could not quit the package setup session %s: %s", session_name, exc)

    log_step("Checking R packages %s (setup session %s)", ", ".join(packages), session_name)
    [(_, installed, error)] = run_in_parallel_browsers(browser_name, launch_args, context_args, ["PKG_"], _worker)
    if error is not None:
        raise RuntimeError("R package setup failed: %s" % error) from error
    return installed
