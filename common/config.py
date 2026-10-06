"""Environment configuration. Loads the repo's .env on import, so `pytest ...`
works on its own in any shell (`source .env` is bash-only and does nothing in
PowerShell).
"""
import os

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(REPO_ROOT, "evidence")

# override=True so a stale value left in the shell (e.g. an old API token)
# can't shadow .env.
load_dotenv(os.path.join(REPO_ROOT, ".env"), override=True)


def env(name, default=None, required=False):
    """Environment variable `name`, else `default`. With required=True,
    raises RuntimeError when it is unset or empty."""
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(
            "environment variable %s is not set. Set it in .env and source it." % name
        )
    return v


def env_list(name, default=None):
    """Comma-separated env var as a list of non-empty, stripped items, or
    `default` when it is unset or empty."""
    v = os.environ.get(name)
    items = [item.strip() for item in v.split(",") if item.strip()] if v else []
    return items or default


def default_session_count():
    """RSTUDIO_SESSION_COUNT (default 10): sessions per multi-session run."""
    return int(env("RSTUDIO_SESSION_COUNT", "10"))


def max_workers_for(count, max_workers=None, env_name="RSTUDIO_CONCURRENT_MAX_WORKERS"):
    """Parallel browsers for a concurrent run of `count` sessions: max_workers,
    else env var `env_name`, else one per session (at least 1)."""
    from_env = env(env_name)
    return max(1, max_workers or (int(from_env) if from_env else None) or count)
