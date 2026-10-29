"""Session naming: every session name prefix the UI tests use and the
functions that build or reserve <prefix><N> names. Each test that can run
alongside another as the same user has its own prefix, so cleanups (by
exact name) never touch each other's sessions.

(The API tests' AUTO_API_SESSION_ names stay in common/api_helper.py and
common/launch_already_created_sessions.py.)
"""
import re

from common import locators

# --- Prefixes ----------------------------------------------------------------
# Default for new sessions (launch_new_session() without a name)
AUTO_PERF_NAME_PREFIX = "AUTO_PERF_SESSION_"
# test_rstudio_run_r_script_perf
RUN_R_SESSION_NAME_PREFIX = "Run_R_session_"
# test_167 / test_167_multiple_times / test_168
TEST_167_NAME_PREFIX = "167_AUTO_SESSION_"
TEST_167_MULTIPLE_TIMES_NAME_PREFIX = "167M_AUTO_SESSION_"
TEST_168_NAME_PREFIX = "168_AUTO_SESSION_"
# test_create_and_launch_multiple_sessions_user1 / _user2: AUTO_SESSION_U<n>_
USER_SESSION_NAME_PREFIX = "AUTO_SESSION_U"
# Concurrent tests
CONCURRENT_SESSION_PREFIX = "CONCURRENT_SESSION_"
U1_CONCURRENT_SESSION_PREFIX = "U1_CONCURRENT_SESSION_"
CONCURRENT_BATCH_SESSION_PREFIX = "CONCURRENT_BATCH_"
CONCURRENT_10MIN_SESSION_PREFIX = "CONCURRENT_10MIN_SESSION_"
CONCURRENT_30MIN_SESSION_PREFIX = "CONCURRENT_30MIN_SESSION_"
# test_concurrent_file_crud (text file create / open / edit / delete loops)
FILE_CRUD_SESSION_PREFIX = "FILE_CRUD_SESSION_"
# test_concurrent_loadfiles
LOAD_FILES_SESSION_PREFIX = "LOAD_FILES_SESSION_"
# test_concurrent_bj
BACKGROUND_JOB_SESSION_PREFIX = "BACKGROUND_JOB_SESSION_"
# test_concurrent_idle
CONCURRENT_IDLE_SESSION_PREFIX = "CONCURRENT_IDLE_SESSION_"
# test_concurrent_scenario_1 (SCENARIO1_<workload>_<N>, common/concurrent_scenario.py)
SCENARIO_1_SESSION_PREFIX = "SCENARIO1_"
U1_CONCURRENT_10MIN_SESSION_PREFIX = "U1_CONCURRENT_10MIN_SESSION_"
U1_CONCURRENT_30MIN_SESSION_PREFIX = "U1_CONCURRENT_30MIN_SESSION_"
# test_concurrent_sessions_10min_s (small 10-minute script)
CONCURRENT_SMALL_10MIN_SESSION_PREFIX = "CONCURRENT_SMALL_10MIN_SESSION_"
# test_mgsolve_10min (mrgsolve_sim.R)
MGSOLVE_10MIN_SESSION_PREFIX = "MGSOLVE_10MIN_SESSION_"
# test_pknca_10min (pknca_nca_10min.R)
PKNCA_10MIN_SESSION_PREFIX = "PKNCA_10MIN_SESSION_"
# test_nlmixr_10min (nlmixr2_sse_10min.R)
NLMIXR_10MIN_SESSION_PREFIX = "NLMIXR_10MIN_SESSION_"
# test_shiny_html_10min (test_html_reports_shiny.R)
SHINY_HTML_10MIN_SESSION_PREFIX = "SHINY_HTML_10MIN_SESSION_"
# test_tidyverse_ggplot_10min (tidyverse_ggplot2_10min.R)
TIDYVERSE_GGPLOT_10MIN_SESSION_PREFIX = "TIDYVERSE_GGPLOT_10MIN_SESSION_"
# test_pmt_10min (all five 10-minute workload scripts, one of each per browser)
PMT_10MIN_SESSION_PREFIX = "PMT_10MIN_"
# Close-tab tests (test_run_r_close_browser, ..._batch_execution)
RUN_R_CLOSE_SESSION_PREFIX = "RUN_R_CLOSE_SESSION_"
BATCH_RUN_R_CLOSE_SESSION_PREFIX = "BATCH_RUN_R_CLOSE_SESSION_"

# Every prefix the UI tests name their sessions with, so a teardown that is
# given no exact names only quits sessions a test could have created, not
# other people's on a shared account.
AUTOMATION_NAME_PREFIXES = (AUTO_PERF_NAME_PREFIX, RUN_R_SESSION_NAME_PREFIX, USER_SESSION_NAME_PREFIX,
                            RUN_R_CLOSE_SESSION_PREFIX, BATCH_RUN_R_CLOSE_SESSION_PREFIX)

# Every prefix any UI test names its sessions with - the default for
# test_delete_sessions (housekeeping)
ALL_SESSION_NAME_PREFIXES = (AUTO_PERF_NAME_PREFIX, RUN_R_SESSION_NAME_PREFIX, USER_SESSION_NAME_PREFIX,
                             TEST_167_NAME_PREFIX, TEST_167_MULTIPLE_TIMES_NAME_PREFIX, TEST_168_NAME_PREFIX,
                             CONCURRENT_SESSION_PREFIX, U1_CONCURRENT_SESSION_PREFIX, CONCURRENT_BATCH_SESSION_PREFIX,
                             CONCURRENT_10MIN_SESSION_PREFIX, CONCURRENT_30MIN_SESSION_PREFIX,
                             U1_CONCURRENT_10MIN_SESSION_PREFIX, U1_CONCURRENT_30MIN_SESSION_PREFIX,
                             CONCURRENT_SMALL_10MIN_SESSION_PREFIX, MGSOLVE_10MIN_SESSION_PREFIX, PKNCA_10MIN_SESSION_PREFIX,
                             NLMIXR_10MIN_SESSION_PREFIX, SHINY_HTML_10MIN_SESSION_PREFIX,
                             TIDYVERSE_GGPLOT_10MIN_SESSION_PREFIX, PMT_10MIN_SESSION_PREFIX,
                             RUN_R_CLOSE_SESSION_PREFIX, BATCH_RUN_R_CLOSE_SESSION_PREFIX, FILE_CRUD_SESSION_PREFIX,
                             LOAD_FILES_SESSION_PREFIX, BACKGROUND_JOB_SESSION_PREFIX, CONCURRENT_IDLE_SESSION_PREFIX,
                             SCENARIO_1_SESSION_PREFIX)


def user_session_name_prefix(user):
    """AUTO_SESSION_U<user>_ (the user1/user2 tests)."""
    return "%s%d_" % (USER_SESSION_NAME_PREFIX, user)


# --- Fixed names ---------------------------------------------------------------
def concurrent_session_names(count, prefix=CONCURRENT_SESSION_PREFIX):
    """[<prefix>1, ..., <prefix><count>]"""
    return ["%s%d" % (prefix, i + 1) for i in range(count)]


def browser_name_prefixes(browser_count):
    """The session name prefix of each browser: "B1_", "B2_", ..."""
    return ["B%d_" % (i + 1) for i in range(browser_count)]


def prefixed_session_names(csv_paths, prefixes=("",)):
    """Every name in `csv_paths` (testdata session name lists) with each of
    `prefixes` in front."""
    from common.api_helper import read_session_names

    return [prefix + name for prefix in prefixes for csv_path in csv_paths for name in read_session_names(csv_path)]


# --- Next free names on the session list -------------------------------------
def highest_number_with_prefix(page, prefix):
    """Highest N among <prefix><N> names on the session list (0 if none)."""
    pattern = re.compile(re.escape(prefix) + r"(\d+)")
    highest = 0
    rows = page.locator(locators.SESSION_ROW_SELECTOR)
    for i in range(rows.count()):
        for match in pattern.finditer(rows.nth(i).inner_text()):
            highest = max(highest, int(match.group(1)))
    return highest


def next_session_names(page, prefix, count, home_url=None):
    """Reserve `count` consecutive <prefix><N> names from one scan of the
    session list, so concurrent browsers don't race for the same name. Pass
    home_url to navigate there first."""
    if home_url:
        from common.rstudio_session_helper import goto_session_list  # (it imports this module)

        goto_session_list(page, home_url)
    start = highest_number_with_prefix(page, prefix) + 1
    return ["%s%d" % (prefix, start + i) for i in range(count)]


def next_session_name(page, prefix, home_url=None):
    """The next free <prefix><N> name (see next_session_names())."""
    return next_session_names(page, prefix, 1, home_url=home_url)[0]


def next_auto_perf_session_names(page, count, home_url=None):
    """next_session_names() with the AUTO_PERF_SESSION_ prefix."""
    return next_session_names(page, AUTO_PERF_NAME_PREFIX, count, home_url=home_url)


def next_run_r_session_names(page, count, home_url=None):
    """Reserve `count` consecutive Run_R_session_<N> names at once."""
    return next_session_names(page, RUN_R_SESSION_NAME_PREFIX, count, home_url=home_url)
