"""Configuration and the phase-00 threshold gate.

A test needing an unset threshold is skipped with a message naming the open
question, rather than silently passing against a number the test invented.
"""
import os
import pytest
import yaml
from dotenv import load_dotenv

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_THRESHOLD_PATH = os.path.join(_HERE, "config", "thresholds.yaml")

# Loads .env into the environment so `pytest ...` works on its own, without a
# shell-specific sourcing step first (the repo's `source .env` is bash-only
# and does nothing in PowerShell). Does not override vars already set.
load_dotenv(os.path.join(_HERE, ".env"))

OPEN_QUESTION = {
    "batch_job_max_seconds": "OQ-01 (AC1)",
    "interactive_p95_degradation_pct": "OQ-02 (AC1)",
    "job_runtime_variance_pct": "OQ-03 (AC2)",
    "session_soak_seconds": "OQ-04 (AC7)",
    "access_methods": "OQ-05 (AC3)",
    "write_visibility_max_seconds": "OQ-07 (AC3)",
    "non_wip_actions": "OQ-08 (AC6)",
    "peak_concurrent_sessions": "OQ-14 (all load profiles)",
}

_CACHE = None


def load():
    global _CACHE
    if _CACHE is None:
        with open(_THRESHOLD_PATH) as fh:
            _CACHE = yaml.safe_load(fh) or {}
    return _CACHE


def is_set(name):
    v = load().get(name)
    return v is not None and v != []


def threshold(name):
    cfg = load()
    if name not in cfg:
        raise KeyError("unknown threshold: %s" % name)
    value = cfg[name]
    if value is None or value == []:
        oq = OPEN_QUESTION.get(name, "see Open Questions sheet")
        pytest.skip(
            "threshold '%s' is not agreed yet (%s). Fill it in "
            "config/thresholds.yaml before this can be a real pass." % (name, oq)
        )
    return value


def get(name, default=None):
    """Non-skipping read, for scripts that run outside pytest."""
    v = load().get(name)
    return default if v is None or v == [] else v


def unset_thresholds():
    cfg = load()
    return [
        (k, oq) for k, oq in OPEN_QUESTION.items()
        if cfg.get(k) is None or cfg.get(k) == []
    ]


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(
            "environment variable %s is not set. Set it in .env and source it." % name
        )
    return v
