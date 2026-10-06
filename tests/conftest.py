import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Open headed browsers maximized (no effect when headless)."""
    args = list(browser_type_launch_args.get("args", []))
    if "--start-maximized" not in args:
        args.append("--start-maximized")
    return {**browser_type_launch_args, "args": args}


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, pytestconfig):
    """With --headed, let pages fill the maximized window instead of the
    fixed 1280x720 viewport. Headless runs keep the default viewport."""
    if not pytestconfig.getoption("--headed", default=False):
        return browser_context_args
    return {**browser_context_args, "no_viewport": True}
