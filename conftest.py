"""Shared pytest fixtures and hooks for the whole suite (project root, next to
pytest.ini): this run's evidence folder, and browser launch/context options."""
import os
import sys

import pytest

# project root (this file's folder), so tests import common.* and config.*
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pytest_configure(config):
    """Every run writes its evidence to its own folder (config.config
    EVIDENCE_DIR, evidence/runs/<time>_<tests>). pytest-playwright's own
    failure screenshots and traces go there too (playwright/), unless
    --output was given (Jenkins passes one per parallel run)."""
    from config.config import EVIDENCE_DIR

    if config.option.collectonly or getattr(config.option, "help", False) or getattr(config.option, "version", False):
        return
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    if config.getoption("--output", default=None) == "test-results":
        config.option.output = os.path.join(EVIDENCE_DIR, "playwright")
    if not hasattr(config, "workerinput"):  # not again in each pytest-xdist worker
        print("\n[rstudio-local] evidence for this run: %s" % EVIDENCE_DIR)


def pytest_terminal_summary(terminalreporter):
    from config.config import EVIDENCE_DIR

    if os.path.isdir(EVIDENCE_DIR):
        terminalreporter.write_line("evidence for this run: %s" % EVIDENCE_DIR)


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
