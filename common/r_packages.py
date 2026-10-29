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
# Repository to install from:
#   auto  (default) Posit Package Manager's precompiled Linux binaries of the
#         CRAN snapshot R_PACKAGES_SNAPSHOT, for the server's OS (read from its
#         /etc/os-release: Ubuntu/Debian by codename, RHEL-likes as rhel<major>).
#         Binaries, as some packages (qs, which rxode2 needs) fail to compile
#         from source on the server, and they install in seconds, not minutes.
#   <URL> that repository, e.g. https://packagemanager.posit.co/cran/2025-03-31
#         for the snapshot's source packages
#   blank the session's own repository (getOption("repos"))
R_PACKAGES_REPO = env("R_PACKAGES_REPO", "auto")
# Snapshot date for R_PACKAGES_REPO=auto: before R 4.5 came out, as the server
# runs R 4.4 and current CRAN versions of some packages (e.g. Deriv, which
# nlmixr2 needs) require R >= 4.5.
R_PACKAGES_SNAPSHOT = env("R_PACKAGES_SNAPSHOT", "2025-03-31")
# Repository to install from when R_PACKAGES_REPO is blank and R has none set
# (getOption("repos") is "@CRAN@", which would open RStudio's mirror chooser).
FALLBACK_REPO = "https://cloud.r-project.org"
# false (default): a package counts as missing only when it is not installed,
# so installed packages are never reinstalled. true: also when its installed
# version differs from R_PACKAGES_REPO's (keeps e.g. the nlmixr2 family on one
# release, at the cost of reinstalling them).
CHECK_VERSIONS = env("R_PACKAGES_CHECK_VERSIONS", "false").lower() == "true"
SETUP_LAUNCH_TIMEOUT_MS = int(env("CONCURRENT_SESSIONS_LAUNCH_TIMEOUT_MS", "180000"))

# The R packages each 10-minute workload script needs (test_<name>_10min and
# test_pmt_10min); each test's <NAME>_R_PACKAGES setting overrides its list.
MGSOLVE_PACKAGES = ["mrgsolve"]
PKNCA_PACKAGES = ["PKNCA", "tidyr"]
# The whole nlmixr2 family: they call each other's internals, so all must come
# from the same release (an older nlmixr2est under a newer nlmixr2 fails every
# fit with "'.nlmixr2objectNameAssign' is not an exported object")
NLMIXR_PACKAGES = ["lotri", "rxode2", "nlmixr2data", "nlmixr2est", "nlmixr2extra", "nlmixr2plot", "nlmixr2"]
SHINY_HTML_PACKAGES = ["rmarkdown", "knitr", "shiny", "callr", "httpuv"]
TIDYVERSE_GGPLOT_PACKAGES = ["tidyverse"]

# (The console commands below have no braces: RStudio's console adds a
# closing one as each opening one is typed.)
_LIB = 'if (nzchar(Sys.getenv("R_LIBS_USER"))) Sys.getenv("R_LIBS_USER") else .libPaths()[1]'


def _r_vector(packages):
    return "c(%s)" % ", ".join(r_string(p) for p in packages)


def _repo_expr():
    """R expression for the R_PACKAGES_REPO repository's URL ("" when blank)."""
    if R_PACKAGES_REPO.lower() != "auto":
        return r_string(R_PACKAGES_REPO)
    # /etc/os-release as a named list of its KEY=value lines, quotes dropped
    # (intToUtf8(34L) is a double quote, kept out of the typed command)
    os_release = ("(function(l) setNames(as.list(gsub(intToUtf8(34L), '', sub('^[^=]*=', '', l))), "
                  "sub('=.*', '', l)))(readLines('/etc/os-release'))")
    return ("(function(os) paste0('https://packagemanager.posit.co/cran/__linux__/', "
            "if (os[['ID']] %%in%% c('ubuntu', 'debian')) os[['VERSION_CODENAME']] "
            "else paste0('rhel', sub('[.].*', '', os[['VERSION_ID']])), '/', %s))(%s)"
            % (r_string(R_PACKAGES_SNAPSHOT), os_release))


def missing_r_packages(page, packages, check_versions=None):
    """The ones of `packages` not installed in any library of the session
    on `page`. With check_versions (default R_PACKAGES_CHECK_VERSIONS) and
    R_PACKAGES_REPO set, also those installed in a version other than that
    repository's (install.packages() keeps a dependency that is already
    there, however old, so a mixed set would otherwise stay mixed)."""
    check_versions = CHECK_VERSIONS if check_versions is None else check_versions
    if check_versions and R_PACKAGES_REPO:
        # (available.packages() downloads the repository's package index)
        expr = ("(function(ap) Filter(function(p) !nzchar(system.file(package=p)) || "
                "isTRUE(as.character(packageVersion(p)) != ap[match(p, rownames(ap)), 'Version']), %s))"
                "(available.packages(repos=%s))" % (_r_vector(packages), _repo_expr()))
        timeout_ms = 120000
    else:
        expr = "Filter(function(p) !nzchar(system.file(package=p)), %s)" % _r_vector(packages)
        timeout_ms = 30000
    value = read_console_value(page, "paste(%s, collapse=',')" % expr, timeout_ms=timeout_ms)
    return [p for p in value.split(",") if p]


def ensure_r_packages(page, packages, timeout_ms=INSTALL_TIMEOUT_MS):
    """Install the missing ones of `packages` (with the packages they depend
    on) into the user's library, in the session on `page`. Returns the ones
    it installed; raises RuntimeError if any are still missing afterwards."""
    missing = missing_r_packages(page, packages)
    if not missing:
        log_step("R packages already installed: %s - nothing to install", ", ".join(packages))
        return []
    installed = [p for p in packages if p not in missing]
    if installed:
        log_step("R packages already installed (left as they are): %s", ", ".join(installed))
    log_step("R packages %s: %s - installing only those (with their dependencies) from %s",
             "missing or not the repository's version" if CHECK_VERSIONS else "not installed",
             ", ".join(missing), {"": "the session's repository",
                                  "auto": "the %s snapshot's Linux binaries" % R_PACKAGES_SNAPSHOT,
                                  }.get(R_PACKAGES_REPO.lower(), R_PACKAGES_REPO))
    if R_PACKAGES_REPO:
        repos = "repos <- %s; " % _repo_expr()
    else:
        repos = "repos <- getOption('repos'); if (any(repos == '@CRAN@')) repos <- %s; " % r_string(FALLBACK_REPO)
    # An install that was stopped or crashed leaves a 00LOCK* folder in the
    # library, and every later install then fails with "failed to lock
    # directory". Only ones older than the install timeout are removed: a
    # newer one can be another machine installing as the same user right now.
    unlock = ("locks <- list.files(lib, '^00LOCK', full.names=TRUE); "
              "unlink(locks[as.numeric(difftime(Sys.time(), file.mtime(locks), units='secs')) > %d], "
              "recursive=TRUE); " % (timeout_ms // 1000))
    # Timed here, not by run_console_command(): its clock starts only once
    # the console has taken the command, which can be well after it is typed
    started = time.time()
    run_console_command(
        page,
        "lib <- %s; dir.create(lib, recursive=TRUE, showWarnings=FALSE); .libPaths(c(lib, .libPaths())); "
        "%s%sinstall.packages(%s, lib=lib, repos=repos, Ncpus=max(1L, parallel::detectCores() - 1L))"
        % (_LIB, unlock, repos, _r_vector(missing)),
        timeout_ms=timeout_ms, poll_interval_ms=5000,
    )
    # Installed now is enough: a dependency install.packages() kept at an
    # older version must not fail the setup
    still = missing_r_packages(page, missing, check_versions=False)
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
