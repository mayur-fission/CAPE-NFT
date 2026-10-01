import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.client import PMTClient  # noqa: E402
from common import config  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "scenario(id): scenario ID from the NFR workbook")
    config.addinivalue_line("markers", "slow: takes more than 10 minutes")
    config.addinivalue_line(
        "markers", "rstudio_local: drives a browser against a local RStudio Workbench instance"
    )


@pytest.fixture(scope="session")
def cfg():
    return config


@pytest.fixture(scope="session")
def client():
    return PMTClient(role="user")


@pytest.fixture(scope="session")
def readonly():
    return PMTClient(role="readonly")


@pytest.fixture(scope="session")
def project_id():
    return config.env("PMT_TEST_PROJECT_ID", required=True)


@pytest.fixture(scope="session")
def metrics():
    """Optional. Load runs work without it; resource-side checks do not."""
    try:
        from common.metrics import get_metrics

        return get_metrics()
    except Exception as exc:
        pytest.skip("metrics backend unavailable: %s" % exc)
