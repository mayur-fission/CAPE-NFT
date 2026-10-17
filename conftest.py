"""Shared pytest fixtures and hooks for the whole suite (project root, next to
pytest.ini): each test file's evidence folder, and browser launch/context
options.

Evidence: every test file of a run writes to its own folder,
evidence/runs/<run start>_<test file>/ (config.config evidence_dir()), so
test files run in parallel (pytest -n) never share one. pytest-playwright's
failure screenshots go to <that folder>/playwright/<test>/, unless --output
is given (Jenkins passes one per parallel branch).
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


def pytest_terminal_summary(terminalreporter):
    """List the evidence folders this run created (one per test file)."""
    from config.config import EVIDENCE_ROOT, RUN_STAMP

    for folder in sorted(glob.glob(os.path.join(EVIDENCE_ROOT, "runs", RUN_STAMP + "_*"))):
        terminalreporter.write_line("evidence: %s" % folder)


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
