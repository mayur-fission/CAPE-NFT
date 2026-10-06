"""Posit Workbench API: launch, suspend, resume and stop sessions without a
browser (POST/GET {server}/api/<method>), plus the testdata/ CSV files that
record which sessions to create and the ids of the ones created.

{server} is WORKBENCH_SERVER_URL, else the host of RSTUDIO_BASE_URL.
`user=N` means RSTUDIO_USER<N> with the bearer token API_TOKEN_USER_<N>.
Run this module directly to print the user's sessions and the user list.
"""

import csv
import itertools
import os
import threading
from urllib.parse import urlsplit

import requests
from openpyxl import Workbook

from common.config import REPO_ROOT, env

_TESTDATA_DIR = os.path.join(REPO_ROOT, "testdata")
_LAUNCH_FIELDS = ("session_id", "project_id", "url")
_SESSION_TIMEOUT_S = 120
DEFAULT_CPU_COUNT = "1"
DEFAULT_MEMORY_MB = "4096"

_session_counter = itertools.count(1)
_counter_lock = threading.Lock()


# -- transport ---------------------------------------------------------


def workbench_server_url():
    """WORKBENCH_SERVER_URL, else scheme://host of RSTUDIO_BASE_URL."""
    url = env("WORKBENCH_SERVER_URL")
    if url:
        return url.rstrip("/")
    parts = urlsplit(env("RSTUDIO_BASE_URL", required=True))
    return "%s://%s" % (parts.scheme, parts.netloc)


def _username(user):
    """RSTUDIO_USER<user>."""
    return env("RSTUDIO_USER%d" % user, required=True)


def _headers(user):
    """JSON headers with user's bearer token (API_TOKEN_USER_<user>)."""
    return {
        "Content-Type": "application/json",
        "Authorization": "Bearer %s" % env("API_TOKEN_USER_%d" % user, required=True),
    }


def _url(method):
    """{server}/api/<method>."""
    return "%s/api/%s" % (workbench_server_url(), method)


def _json(resp):
    """Parsed JSON body ({} when empty); raises requests.HTTPError on error."""
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _post(method, user=1, kwparams=None, timeout=60):
    """POST {"method", "kwparams"} as `user` and return the parsed reply."""
    body = {"method": method}
    if kwparams is not None:
        body["kwparams"] = kwparams
    return _json(
        requests.post(_url(method), headers=_headers(user), json=body, timeout=timeout)
    )


def _get(method, user=1, params=None, timeout=60):
    """GET with query `params` as `user` and return the parsed reply."""
    return _json(
        requests.get(
            _url(method), headers=_headers(user), params=params, timeout=timeout
        )
    )


# -- lookups -----------------------------------------------------------


def get_users(user=1):
    """GET /api/get_users."""
    return _get("get_users", user)


def get_sessions(user=1):
    """POST /api/get_session."""
    return _post("get_session", user)


# -- session lifecycle: launch -> suspend -> resume -> stop --------------


def next_session_name(prefix="AUTO_API_SESSION_"):
    """AUTO_API_SESSION_<N>, unique within this process."""
    with _counter_lock:
        return "%s%d" % (prefix, next(_session_counter))


def _launch_parameters(name, cpu_count, memory_mb, launch=False):
    """launch_parameters for resume; launch=True adds the container fields."""
    params = {
        "name": name,
        "cluster": "Local",
        "placement_constraints": [],
        "resource_limits": [
            {"type": "cpuCount", "value": str(cpu_count)},
            {"type": "memory", "value": str(memory_mb)},
        ],
        "queues": [""],
        "resource_profile": "default",
    }
    if launch:
        params.update(
            cluster_type="Local",
            container_image="",
            default_image="",
            container_images=[""],
        )
    return params


def launch_session(
    name=None,
    user=1,
    working_directory=None,
    cpu_count=DEFAULT_CPU_COUNT,
    memory_mb=DEFAULT_MEMORY_MB,
):
    """POST /api/launch_session. working_directory defaults to RSTUDIO_WORKING_DIR."""
    kwparams = {
        "workbench": "RStudio",
        "username": _username(user),
        "working_directory": working_directory
        or env("RSTUDIO_WORKING_DIR", required=True),
        "launch_parameters": _launch_parameters(
            name or next_session_name(), cpu_count, memory_mb, launch=True
        ),
    }
    return _post("launch_session", user, kwparams, _SESSION_TIMEOUT_S)


def _stop_session(session_ids, user, suspend, force_quit):
    """POST /api/stop_session; session_ids is a list or comma-separated string."""
    if not isinstance(session_ids, str):
        session_ids = ",".join(session_ids)
    kwparams = {
        "session_ids": session_ids,
        "force_quit": force_quit,
        "suspend": suspend,
    }
    return _post("stop_session", user, kwparams, _SESSION_TIMEOUT_S)


def suspend_session(session_ids, user=1):
    """Save state and exit, so the sessions can be resumed."""
    return _stop_session(session_ids, user, suspend=True, force_quit=False)


def resume_session(
    session_id,
    name,
    user=1,
    cpu_count=DEFAULT_CPU_COUNT,
    memory_mb=DEFAULT_MEMORY_MB,
):
    """POST /api/resume_session for a suspended session."""
    kwparams = {
        "username": _username(user),
        "session_id": session_id,
        "launch_parameters": _launch_parameters(name, cpu_count, memory_mb),
    }
    return _post("resume_session", user, kwparams, _SESSION_TIMEOUT_S)


def stop_session(session_ids, user=1, force_quit=False):
    """Stop without saving state; force_quit=True kills the process."""
    return _stop_session(session_ids, user, suspend=False, force_quit=force_quit)


def _session_fields(session):
    """{session_id, project_id, url} from a session or launch/resume result."""
    return {
        "session_id": session["id"],
        "project_id": session.get("project_id", ""),
        "url": session.get("url", ""),
    }


def launched_session_from(response):
    """{session_id, project_id, url} from a launch/resume response."""
    result = response.get("result") or {}
    if not result.get("id"):
        raise ValueError("no session id in response: %r" % response)
    return _session_fields(result)


# -- session status ------------------------------------------------------

RUNNING = "running"
MISSING = "missing"  # named in the CSV but not on the server
# on their way to running (or to suspended): leave alone
STARTING_STATES = {"launching", "resuming", "pending", "starting", "suspending"}
# brought back with resume_session
RESUMABLE_STATES = {"suspended"}
# gone for good: a new session is launched with the same name
ENDED_STATES = {"finished", "failed", "killed", MISSING}


def list_sessions(user=1):
    """The user's sessions from get_session."""
    return (get_sessions(user).get("result") or {}).get("sessions") or []


def session_name_of(session):
    """A get_session entry's name (label, display name or launch name)."""
    return (
        session.get("label")
        or session.get("display_name")
        or (session.get("launch_parameters") or {}).get("name")
    )


def _session_rank(session):
    """Higher is better when several sessions share a name."""
    state = session.get("activity_state")
    return (state == RUNNING, state in STARTING_STATES, session.get("created") or 0)


def sessions_by_name(user=1):
    """{name: session}, keeping the best-ranked session for each name."""
    by_name = {}
    for session in list_sessions(user):
        name = session_name_of(session)
        if name not in by_name or _session_rank(session) > _session_rank(by_name[name]):
            by_name[name] = session
    return by_name


def csv_session_states(csv_path, user=1, live=None):
    """[(name, session or None, state)] for each name in csv_path.

    live is a sessions_by_name() result, fetched if not given.
    """
    live = sessions_by_name(user) if live is None else live
    states = []
    for name in read_session_names(csv_path):
        session = live.get(name)
        states.append(
            (name, session, session["activity_state"] if session else MISSING)
        )
    return states


# -- CSV files -----------------------------------------------------------


def read_session_names(csv_path):
    """Names from the `session_name` column."""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return [
            row["session_name"].strip()
            for row in csv.DictReader(fh)
            if row.get("session_name", "").strip()
        ]


def get_testdata_path(file_name):
    """testdata/<file_name>."""
    return os.path.join(_TESTDATA_DIR, file_name)


def session_names_csv_path(user):
    """testdata/session_names_U<user>.csv: the names to create for `user`."""
    return get_testdata_path("session_names_U%d.csv" % user)


def group_sessions_csv_paths(groups):
    """{group: testdata/group_<group>_sessions.csv} for each group."""
    return {g: get_testdata_path("group_%s_sessions.csv" % g) for g in groups}


def session_ids_csv_path(user, ids_csv_path=None):
    """ids_csv_path, else testdata/session_ids_U<user>.csv."""
    return ids_csv_path or os.path.join(_TESTDATA_DIR, "session_ids_U%d.csv" % user)


def write_session_ids(csv_path, sessions):
    """Overwrite csv_path with [{session_name, session_id, project_id, url}]."""
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=("session_name",) + _LAUNCH_FIELDS)
        writer.writeheader()
        writer.writerows(sessions)


def update_session_ids(csv_path, sessions):
    """Add or replace rows in csv_path by session_name, keeping the others."""
    by_name = {}
    if os.path.exists(csv_path):
        by_name = {s["session_name"]: s for s in read_launched_sessions(csv_path)}
    by_name.update((s["session_name"], s) for s in sessions)
    write_session_ids(csv_path, list(by_name.values()))


def read_launched_sessions(csv_path):
    """[{session_name, session_id, project_id, url}] from write_session_ids."""
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def session_projects_xlsx_path(user, xlsx_path=None):
    """xlsx_path, else testdata/session_projects_U<user>.xlsx."""
    return xlsx_path or os.path.join(_TESTDATA_DIR, "session_projects_U%d.xlsx" % user)


def write_session_projects_xlsx(xlsx_path, sessions):
    """Overwrite xlsx_path with the session_name and project_id of sessions."""
    wb = Workbook()
    ws = wb.active
    ws.title = "sessions"
    ws.append(["session_name", "project_id"])
    for s in sessions:
        ws.append([s["session_name"], s["project_id"]])
    wb.save(xlsx_path)


# -- bulk, per user: launch -> suspend -> resume -> stop -----------------


def _for_each_session(sessions, verb, call):
    """call(session) for each session, one at a time, past failures.

    Returns (done, failed): done is [{session_name, session_id, project_id,
    url}] from the responses, failed is [(name, error)].
    """
    done, failed = [], []
    for i, session in enumerate(sessions, 1):
        name = session["session_name"]
        tag = "[%d/%d]" % (i, len(sessions))
        try:
            row = dict(session_name=name, **launched_session_from(call(session)))
        except (requests.RequestException, ValueError) as exc:
            failed.append((name, exc))
            print("%s FAILED %s: %s" % (tag, name, exc))
            continue
        done.append(row)
        print("%s %s %s (id %s)" % (tag, verb, name, row["session_id"]))
    return done, failed


def _all_sessions_at_once(user, ids_csv_path, verb, call):
    """call(session_ids) once for every saved session. Returns the ids."""
    csv_path = session_ids_csv_path(user, ids_csv_path)
    session_ids = [s["session_id"] for s in read_launched_sessions(csv_path)]
    if session_ids:
        call(session_ids)
        print("%s %d session(s): %s" % (verb, len(session_ids), ",".join(session_ids)))
    return session_ids


def launch_sessions_from_csv(csv_path, user=1, ids_csv_path=None):
    """Launch one session per name; save ids. Returns (launched, failed)."""
    sessions = [{"session_name": name} for name in read_session_names(csv_path)]
    launched, failed = _for_each_session(
        sessions, "launched", lambda s: launch_session(s["session_name"], user)
    )
    ids_csv_path = session_ids_csv_path(user, ids_csv_path)
    write_session_ids(ids_csv_path, launched)
    print("session ids written to %s" % ids_csv_path)
    return launched, failed


def suspend_sessions_from_csv(user=1, ids_csv_path=None):
    """Suspend all saved sessions in one call. Returns their ids."""
    return _all_sessions_at_once(
        user, ids_csv_path, "suspended", lambda ids: suspend_session(ids, user)
    )


def resume_sessions_from_csv(user=1, ids_csv_path=None):
    """Resume each saved session; update the file. Returns (resumed, failed)."""
    ids_csv_path = session_ids_csv_path(user, ids_csv_path)
    sessions = read_launched_sessions(ids_csv_path)
    resumed, failed = _for_each_session(
        sessions,
        "resumed",
        lambda s: resume_session(s["session_id"], s["session_name"], user),
    )
    by_name = {s["session_name"]: s for s in resumed}
    write_session_ids(
        ids_csv_path, [by_name.get(s["session_name"], s) for s in sessions]
    )
    return resumed, failed


def stop_sessions_from_csv(user=1, force_quit=False, ids_csv_path=None):
    """Stop all saved sessions in one call. Returns their ids."""
    return _all_sessions_at_once(
        user,
        ids_csv_path,
        "stopped",
        lambda ids: stop_session(ids, user, force_quit=force_quit),
    )


def launch_missing_or_ended_sessions(csv_path, user=1, ids_csv_path=None, live=None):
    """Launch a new session for each CSV name that is missing, finished,
    failed or killed; save the new ids. Returns (launched, failed).
    """
    targets = [
        {"session_name": name}
        for name, _, state in csv_session_states(csv_path, user, live)
        if state in ENDED_STATES
    ]
    launched, failed = _for_each_session(
        targets, "launched", lambda s: launch_session(s["session_name"], user)
    )
    update_session_ids(session_ids_csv_path(user, ids_csv_path), launched)
    return launched, failed


def resume_suspended_sessions(csv_path, user=1, ids_csv_path=None, live=None):
    """Resume each CSV name whose session is suspended; save the ids.
    Returns (resumed, failed).
    """
    targets = [
        dict(session_name=name, **_session_fields(session))
        for name, session, state in csv_session_states(csv_path, user, live)
        if state in RESUMABLE_STATES
    ]
    resumed, failed = _for_each_session(
        targets,
        "resumed",
        lambda s: resume_session(s["session_id"], s["session_name"], user),
    )
    update_session_ids(session_ids_csv_path(user, ids_csv_path), resumed)
    return resumed, failed


def check_and_relaunch_inactive_sessions(
    csv_path, user=1, ids_csv_path=None, xlsx_path=None
):
    """Bring every session named in csv_path back to running.

    Running/starting sessions are left alone, suspended ones are resumed,
    and missing/finished/failed/killed ones are launched as new sessions.
    Session names and project ids go to xlsx_path (default
    testdata/session_projects_U<user>.xlsx).

    Returns {"running", "starting", "resumed", "launched": [names],
    "failed": [(name, error)]}.
    """
    ids_csv_path = session_ids_csv_path(user, ids_csv_path)
    live = sessions_by_name(user)
    states = csv_session_states(csv_path, user, live)
    for name, _, state in states:
        print("%s: %s" % (name, state))

    active = [
        (name, session, state)
        for name, session, state in states
        if state == RUNNING or state in STARTING_STATES
    ]
    active_rows = [
        dict(session_name=name, **_session_fields(s)) for name, s, _ in active
    ]
    update_session_ids(ids_csv_path, active_rows)
    resumed, resume_failed = resume_suspended_sessions(
        csv_path, user, ids_csv_path, live
    )
    launched, launch_failed = launch_missing_or_ended_sessions(
        csv_path, user, ids_csv_path, live
    )
    print("session ids written to %s" % ids_csv_path)

    by_name = {s["session_name"]: s for s in active_rows + resumed + launched}
    xlsx_path = session_projects_xlsx_path(user, xlsx_path)
    write_session_projects_xlsx(
        xlsx_path, [by_name[name] for name, _, _ in states if name in by_name]
    )
    print("session names and project ids written to %s" % xlsx_path)
    return {
        "running": [name for name, _, state in active if state == RUNNING],
        "starting": [name for name, _, state in active if state != RUNNING],
        "resumed": [s["session_name"] for s in resumed],
        "launched": [s["session_name"] for s in launched],
        "failed": resume_failed + launch_failed,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(get_sessions(), indent=2))
    print(json.dumps(get_users(), indent=2))
