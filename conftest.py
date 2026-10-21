"""Shared pytest fixtures and hooks for the whole suite (project root, next to
pytest.ini): each test file's evidence folder, and browser launch/context
options.

Evidence: every test file of a run writes to its own folder,
evidence/runs/<run start>_<test file>/ (config.config evidence_dir()), so
test files run in parallel (pytest -n) never share one. pytest-playwright's
failure screenshots go to <that folder>/playwright/<test>/, unless --output
is given (Jenkins passes one per parallel branch). Each folder also gets
step_log.txt (every step, written live - common/step_log.py) and
test_output.txt (everything each test printed, written after each phase).

Allure: a local run (no --alluredir) saves its results and builds one
self-contained report, evidence/runs/<run start>_allure/allure-report.html,
at the end (pytest_configure / pytest_sessionfinish below). Jenkins passes
its own --alluredir and builds its report itself.
"""
import glob
import os
import re
import sys

import pytest

# project root (this file's folder), so tests import common.* and config.*
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Loads .env and fixes this run's start time (config.config RUN_STAMP) now, in the
# main pytest process, so pytest-xdist workers - started later - inherit it and
# all test files of the run get folders with the same time stamp.
import config.config  # noqa: E402,F401


@pytest.fixture
def output_path(pytestconfig, request):
    """pytest-playwright's artifact folder for this test: inside the test
    file's evidence folder, unless --output was given."""
    output = pytestconfig.getoption("--output")
    test_file = os.path.splitext(os.path.basename(str(request.node.fspath)))[0]
    name = re.sub(r"[^\w.-]", "-", request.node.nodeid)[:150]
    if output != "test-results":
        return os.path.join(os.path.abspath(output), name)
    from config.config import evidence_dir

    return os.path.join(evidence_dir(test_file), "playwright", name)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """After each test phase, append its captured output (stdout, stderr,
    log) to test_output.txt in the test file's evidence folder - everything
    the test printed, also the lines that are not in step_log.txt (see
    common/step_log.py). Runs where the test runs (also in an xdist worker),
    so nothing is written twice. Empty with -s (nothing is captured)."""
    outcome = yield
    report = outcome.get_result()
    # report.sections also repeats the earlier phases ("Captured stdout setup"
    # in the call report): keep only this phase's
    sections = [(name, text) for name, text in report.sections
                if name.endswith(" " + report.when) and text.strip()]
    if not sections:
        return
    try:
        from config.config import evidence_dir

        test_file = os.path.splitext(os.path.basename(str(item.fspath)))[0]
        path = os.path.join(evidence_dir(test_file), "test_output.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write("===== %s (%s: %s) =====\n" % (item.nodeid, report.when, report.outcome))
            for name, text in sections:
                f.write("----- %s -----\n%s\n" % (name, text.rstrip("\n")))
    except Exception as exc:
        print("could not write test_output.txt: %s" % exc)


LOCAL_ALLURE_FOLDER = "allure"


def _local_allure_dir():
    """evidence/runs/<run start>_allure/: this run's local Allure results
    (results/) and report (allure-report.html)."""
    from config.config import EVIDENCE_ROOT, RUN_STAMP

    return os.path.join(EVIDENCE_ROOT, "runs", "%s_%s" % (RUN_STAMP, LOCAL_ALLURE_FOLDER))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Local runs (VS Code, a terminal): when no --alluredir is given, save
    the Allure results under evidence/runs/<run start>_allure/results/, so
    pytest_sessionfinish() can build the report from them. Runs before the
    Allure plugin reads the option. Jenkins passes its own --alluredir and
    builds its own report, so it is left alone; LOCAL_ALLURE_REPORT=0 turns
    this off."""
    from config.config import env

    if not hasattr(config.option, "allure_report_dir") or config.option.allure_report_dir:
        return  # no allure-pytest, or --alluredir given (Jenkins)
    if config.option.collectonly:
        return
    if env("LOCAL_ALLURE_REPORT", "1") in ("", "0", "false", "False", "no"):
        return
    config.option.allure_report_dir = os.path.join(_local_allure_dir(), "results")
    config._local_allure_report = True


def pytest_sessionfinish(session):
    """After a local run (see pytest_configure()), build the Allure report as
    one self-contained evidence/runs/<run start>_allure/allure-report.html
    (opens from disk, like Jenkins' allure-report.html) with the Allure
    command line (`allure` on PATH, or ALLURE_CMD) - needs Java. Only in the
    main process (not in xdist workers), only when results were written.
    Never fails the run."""
    import shutil
    import subprocess

    pytest_config = session.config
    if not getattr(pytest_config, "_local_allure_report", False) or hasattr(pytest_config, "workerinput"):
        return
    results = pytest_config.option.allure_report_dir
    if not os.path.isdir(results) or not os.listdir(results):
        return
    from config.config import env

    allure = env("ALLURE_CMD") or shutil.which("allure")
    if not allure:
        pytest_config._local_allure_message = (
            "Allure results saved to %s - no Allure report built: install the Allure command line "
            "(e.g. scoop install allure) or set ALLURE_CMD" % results)
        return
    out_dir = os.path.join(_local_allure_dir(), "report")
    report = os.path.join(_local_allure_dir(), "allure-report.html")
    try:
        done = subprocess.run([allure, "generate", "--single-file", results, "--clean", "-o", out_dir],
                              capture_output=True, text=True, timeout=600)
        if done.returncode != 0:
            raise RuntimeError((done.stderr or done.stdout).strip()[-500:])
        os.replace(os.path.join(out_dir, "index.html"), report)
        shutil.rmtree(out_dir, ignore_errors=True)
        pytest_config._local_allure_message = "Allure report: %s" % report
    except Exception as exc:
        pytest_config._local_allure_message = "Could not build the Allure report from %s: %s" % (results, exc)


def pytest_terminal_summary(terminalreporter):
    """List the evidence folders this run created (one per test file), and
    the local Allure report (see pytest_sessionfinish())."""
    from config.config import EVIDENCE_ROOT, RUN_STAMP

    for folder in sorted(glob.glob(os.path.join(EVIDENCE_ROOT, "runs", RUN_STAMP + "_*"))):
        terminalreporter.write_line("evidence: %s" % folder)
    message = getattr(terminalreporter.config, "_local_allure_message", None)
    if message:
        terminalreporter.write_line(message, bold=True)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Open headed browsers maximized (no effect when headless)."""
    from config.config import browser_window_args

    args = list(browser_type_launch_args.get("args", []))
    args += [a for a in browser_window_args() if a not in args]
    return {**browser_type_launch_args, "args": args}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, pytestconfig):
    """With --headed, let pages fill the maximized window instead of the
    fixed 1280x720 viewport. Headless runs keep the default viewport."""
    if not pytestconfig.getoption("--headed", default=False):
        return browser_context_args
    return {**browser_context_args, "no_viewport": True}
