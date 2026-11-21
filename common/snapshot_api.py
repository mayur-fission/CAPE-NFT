"""Project snapshot API: trigger a snapshot, poll its status until it is
done, read its details - and record how long it took
(tests/test_project_snapshot_time.py).

Base URL: API_BASE_URL in .env (required), e.g. https://<host>. Auth:
"Authorization: Bearer <TOKEN>" with TOKEN from .env (git ignores .env). A JWT's
expiry is checked up front. On HTTP 401/403 (token rejected or expired)
SnapshotApi.on_auth_error, when set, is asked for a new token and the request
is repeated with it (the test asks the user on the console - see
common/snapshot_token.py); otherwise it fails at once.

    API 1  trigger  POST /api/v1/projects/{project_id}/snapshots
                    {"name": ..., "comment": ..., "is_major": false} -> snapshot id
    API 2  status   GET /api/v1/projects/{project_id}/snapshot-jobs/{snapshot_id}
                                                                     -> status
    API 3  list     GET /api/v1/projects/{project_id}/snapshots?page=1&page_size=20&sort=-snapshot_id
                    -> every snapshot of the project, newest first: ours (found by its
                       name) must show the name, comment and is_major we sent

The snapshot time is end - start from API 2's response on success.

The snapshot id API 2 uses is taken from API 1's response: the field
SNAPSHOT_ID_FIELD names (dotted for nested, e.g. data.id), else the first of
SNAPSHOT_ID_FIELDS found.

TODO(API specs): all three paths are the real ones. Still guessed (from
common names) until response samples are in: which field of API 1's
response holds the id, API 2's status field, status values and start/end
fields, and the field names of API 3's entries. Each path can also be set
in .env without a code change. Response fields are looked up at the top
level and inside "result" / "data", under each of the names listed.
"""
import base64
import csv
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import requests

from common.perf_report import attach_allure_file, write_json_report
from common.step_log import log_step
from config.config import EVIDENCE_ROOT, RUN_STAMP, env, evidence_dir

# -- endpoints (status/details are placeholders, see TODO above) ---------
TRIGGER_METHOD = env("SNAPSHOT_TRIGGER_METHOD") or "POST"
TRIGGER_PATH = env("SNAPSHOT_TRIGGER_PATH") or "/api/v1/projects/{project_id}/snapshots"
STATUS_PATH = env("SNAPSHOT_STATUS_PATH") or "/api/v1/projects/{project_id}/snapshot-jobs/{snapshot_id}"
LIST_PATH = env("SNAPSHOT_LIST_PATH") or "/api/v1/projects/{project_id}/snapshots"
LIST_PAGE_SIZE = int(env("SNAPSHOT_LIST_PAGE_SIZE") or "20")
LIST_SORT = env("SNAPSHOT_LIST_SORT") or "-snapshot_id"
# Pages of the list searched for our snapshot (newest first, so it is on page 1)
LIST_MAX_PAGES = int(env("SNAPSHOT_LIST_MAX_PAGES") or "5")
# Reads of the list, LIST_RETRY_S apart, before "not in the list" fails
# API 2 can say "succeeded" while its "listing" is still "generating", so the list may lag behind
LIST_ATTEMPTS = int(env("SNAPSHOT_LIST_ATTEMPTS") or "6")
LIST_RETRY_S = float(env("SNAPSHOT_LIST_RETRY_S") or "10")
# Where a list response keeps its entries (or the response is the list itself)
LIST_ITEMS_FIELDS = ("items", "results", "data", "snapshots", "content", "records")
NAME_FIELDS = ("name", "snapshot_name")
COMMENT_FIELDS = ("comment", "description")
IS_MAJOR_FIELDS = ("is_major", "isMajor")
LISTED_SNAPSHOT_ID_FIELDS = ("snapshot_id", "snapshotId")

# -- project -----------------------------------------------------------------
# A project's folder: CAPE_<year>_<month>_<project id>, e.g. CAPE_2026_11_0223 -> 223
PROJECT_FOLDER_RE = re.compile(r"CAPE_\d{4}_\d{2}_(\d+)", re.IGNORECASE)

# -- API 1 body ----------------------------------------------------------------
# name: SNAPSHOT_NAME, else "NFR <run start>" (unique per run)
SNAPSHOT_NAME = env("SNAPSHOT_NAME") or "NFR %s" % RUN_STAMP
SNAPSHOT_COMMENT = env("SNAPSHOT_COMMENT") or "TEST"
SNAPSHOT_IS_MAJOR = (env("SNAPSHOT_IS_MAJOR") or "false").strip().lower() in ("1", "true", "yes")

# -- response fields (first name found wins) -----------------------------
# API 2 is .../snapshot-jobs/<id>, so a job id is preferred over a snapshot id
SNAPSHOT_ID_FIELDS = ("job_id", "jobId", "snapshot_job_id", "snapshotJobId", "id", "snapshot_id", "snapshotId")
# The exact field of API 1's response holding that id, e.g. "id" or "data.job_id" (overrides the list)
SNAPSHOT_ID_FIELD = env("SNAPSHOT_ID_FIELD")
PROJECT_ID_FIELDS = ("project_id", "projectId")
STATUS_FIELDS = ("status", "state")
# API 1's link to the job's status (API 2), e.g. "/api/v1/projects/223/snapshot-jobs/2389"
STATUS_URL_FIELDS = ("status_url", "statusUrl")
# Start / end time of the snapshot in API 2's (status) response: the snapshot time is end - start.
# SNAPSHOT_START_FIELD / SNAPSHOT_END_FIELD name the exact fields (dotted for nested, e.g. data.start_time).
# API 2 gives "started_at": "2026-11-20T12:39:24.244240Z" and "finished_at" (null until it ends);
# "requested_at" is when API 1 was called and is not used. The other names are fallbacks.
STARTED_FIELDS = ("started_at", "start_time", "startTime", "startedAt", "start")
ENDED_FIELDS = ("finished_at", "end_time", "ended_at", "completed_at", "endTime", "endedAt", "completedAt",
                "finishedAt", "end")
# API 2's progress, logged with each poll: "step": "cloning", "step_number": 2, "step_count": 6
STEP_FIELDS = ("step",)
STEP_NUMBER_FIELDS = ("step_number",)
STEP_COUNT_FIELDS = ("step_count",)
ERROR_FIELDS = ("error", "error_message", "message")
# API 2's "snapshot" (null until done): the created snapshot, or its id
SNAPSHOT_OBJECT_FIELDS = ("snapshot",)
SNAPSHOT_START_FIELD = env("SNAPSHOT_START_FIELD")
SNAPSHOT_END_FIELD = env("SNAPSHOT_END_FIELD")
_WRAPPERS = ("result", "data")

# -- status values (compared in lower case) ------------------------------
SUCCESS_STATUSES = frozenset({"success", "succeeded", "completed"})
FAILED_STATUSES = frozenset({"failed", "failure", "error", "cancelled", "canceled"})

# A status poll that fails (network, 5xx such as a 502 from the gateway while the backend
# restarts) is retried at the next interval; this many failures in a row fail the wait
# (30 x 10s = 5 min). The snapshot keeps running on the server meanwhile.
MAX_POLL_ERRORS = int(env("SNAPSHOT_MAX_POLL_ERRORS") or "30")
REQUEST_TIMEOUT_S = 60

# Shared by every run, so runs can be compared (one row per run)
RESULTS_CSV = os.path.join(EVIDENCE_ROOT, "snapshot_times.csv")
# The job's longest steps, given their own columns (seconds, from the poll history - see step_durations())
CRITICAL_STEPS = ("committing", "uploading")
RESULT_FIELDS = ("run_stamp", "test", "scenario", "target_gb", "status", "project_id", "snapshot_id",
                 "created_snapshot_id", "snapshot_version", "data_action", "data_gb", "data_percent",
                 "data_files", "data_seconds",
                 "snapshot_size", "queue_wait_s", "snapshot_duration_s", "committing_s", "uploading_s",
                 "snapshot_started_at", "snapshot_ended_at",
                 "client_duration_s", "polls", "poll_interval_s", "list_check", "triggered_at_utc", "error")


class SnapshotError(AssertionError):
    """A snapshot did not complete; the message says why."""


class SnapshotFailed(SnapshotError):
    """The snapshot reached a failed, error or cancelled status."""


class SnapshotTimeout(SnapshotError):
    """The snapshot was not successful within the timeout."""


class SnapshotAuthError(SnapshotError):
    """The bearer token is missing, expired or rejected (HTTP 401/403)."""


def lookup(body: dict, path: str):
    """The value at the dotted `path` in `body` (e.g. "data.id"), or None."""
    value = body
    for key in path.split("."):
        if not isinstance(value, dict) or value.get(key) in (None, ""):
            return None
        value = value[key]
    return value


def snapshot_id_from(body: dict):
    """The snapshot id in API 1's response: the SNAPSHOT_ID_FIELD path
    (dots for nesting) if set, else field(body, SNAPSHOT_ID_FIELDS); None
    if it is not there."""
    return lookup(body, SNAPSHOT_ID_FIELD) if SNAPSHOT_ID_FIELD else field(body, SNAPSHOT_ID_FIELDS)


def list_items(body) -> Optional[list]:
    """The entries of a list response: the response itself when it is a
    list, else the first of LIST_ITEMS_FIELDS holding a list; None if none."""
    if isinstance(body, list):
        return body
    for name in LIST_ITEMS_FIELDS:
        if isinstance(body.get(name), list):
            return body[name]
    return None


def listed_snapshot_mismatches(item: dict, expected: dict) -> list:
    """What in our snapshot list entry `item` differs from `expected`:
    {"name", "comment", "is_major"} must be in the entry and equal;
    "snapshot_id", "project_id" and "status" are checked when both
    `expected` and the entry have them. [] when all match."""
    checks = [("name", NAME_FIELDS, True), ("comment", COMMENT_FIELDS, True), ("is_major", IS_MAJOR_FIELDS, True),
              ("snapshot_id", LISTED_SNAPSHOT_ID_FIELDS, False), ("project_id", PROJECT_ID_FIELDS, False)]
    problems = []
    for key, names, required in checks:
        want = expected.get(key)
        got = field(item, names)
        if want is None or (got is None and not required):
            continue
        if got is None:
            problems.append("%s missing (looked for %s)" % (key, "/".join(names)))
        elif str(got).lower() != str(want).lower():
            problems.append("%s is %r, expected %r" % (key, got, want))
    status = field(item, STATUS_FIELDS)
    if status is not None and str(status).strip().lower() not in SUCCESS_STATUSES:
        problems.append("status is %r, expected one of %s" % (status, sorted(SUCCESS_STATUSES)))
    return problems


def snapshot_times(body: dict):
    """(start, end) as given in a status response (SNAPSHOT_START_FIELD /
    SNAPSHOT_END_FIELD, else the first of STARTED_FIELDS / ENDED_FIELDS);
    either is None when missing."""
    start = lookup(body, SNAPSHOT_START_FIELD) if SNAPSHOT_START_FIELD else field(body, STARTED_FIELDS)
    end = lookup(body, SNAPSHOT_END_FIELD) if SNAPSHOT_END_FIELD else field(body, ENDED_FIELDS)
    return start, end


def project_id_from_path(path: Optional[str]) -> Optional[str]:
    """The project id in a project folder path: the number after the last
    "_" of its CAPE_<year>_<month>_<number> folder, without leading zeros -
    /mnt/fsx/CAPE_2026_11_0223/... -> "223". None if no folder matches."""
    for part in reversed((path or "").replace("\\", "/").split("/")):
        match = PROJECT_FOLDER_RE.fullmatch(part)
        if match:
            return str(int(match.group(1)))
    return None


def clean_token(value: Optional[str]) -> str:
    """`value` without spaces, quotes or a "Bearer " prefix around it."""
    token = (value or "").strip().strip("\"'").strip()
    if token.lower().startswith("bearer "):
        token = token[len("bearer "):].strip()
    return token


def token_expiry(token: str) -> Optional[datetime]:
    """When the JWT `token` expires (its "exp" claim, UTC), read without
    verifying it; None if the token is not a JWT or has no exp."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return datetime.fromtimestamp(float(claims["exp"]), timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None


def field(body: dict, names, default=None):
    """The first of `names` found in `body`, or in body["result"] /
    body["data"]; `default` if none is."""
    for scope in [body] + [body.get(w) for w in _WRAPPERS if isinstance(body.get(w), dict)]:
        for name in names:
            if scope.get(name) not in (None, ""):
                return scope[name]
    return default


class SnapshotApi:
    """The snapshot API as one user (bearer token).

    trigger_snapshot() notes the time.monotonic() just before its request in
    `triggered_at`; wait_until_complete() measures from there.
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None,
                 timeout_s: int = REQUEST_TIMEOUT_S):
        self.base_url = (base_url or env("API_BASE_URL") or "").strip().rstrip("/")
        if not self.base_url:
            raise SnapshotError("API_BASE_URL is not set: put the snapshot API base URL in .env, "
                                "e.g. API_BASE_URL=https://<host>")
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
        self.token_expires_at: Optional[datetime] = None
        self.set_token(token or env("TOKEN"))
        # Called with the reason when the API rejects the token (HTTP 401/403): returns a new token to
        # repeat the request with, or None to fail (the test asks the user - common/snapshot_token.py)
        self.on_auth_error: Optional[Callable[[str], Optional[str]]] = None
        # Called with a replacement token once a request with it succeeded (e.g. to save it)
        self.on_new_token: Optional[Callable[[str], None]] = None
        self.triggered_at: Optional[float] = None
        self.polls = 0
        # one entry per status poll: {"poll", "elapsed_s", "at_utc", "state", "step", "step_number",
        # "step_count"} or {"poll", "elapsed_s", "at_utc", "error"} - the report's step breakdown
        self.poll_history: list = []
        self.project_id: Optional[str] = None
        self.trigger_response: Optional[dict] = None
        self.last_status: Optional[dict] = None
        # job id -> the trigger response's status_url (a path under base_url)
        self.status_urls: dict = {}
        self.last_list: Optional[dict] = None  # the last snapshot list page (API 3), for its "total"

    def set_token(self, token: Optional[str]):
        """Use `token` (a "Bearer " prefix and quotes are dropped) for the
        requests from now on. Raises SnapshotAuthError when it is empty, or a
        JWT that has already expired. The token is never logged."""
        token = clean_token(token)
        if not token:
            raise SnapshotAuthError("TOKEN is not set: put the snapshot API bearer token in .env as TOKEN=<token>")
        expires_at = token_expiry(token)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            raise SnapshotAuthError("the token expired at %s UTC" % expires_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.token_expires_at = expires_at
        self.session.headers["Authorization"] = "Bearer %s" % token
        left = self.token_seconds_left()
        if left is not None:
            log_step("Snapshot API token is valid for %.0f more minutes", left / 60.0)

    def check_token(self, project_id: Optional[str] = None):
        """Read the first page of the snapshot list: raises SnapshotAuthError
        when the API rejects the token (on_auth_error is not asked)."""
        ask, self.on_auth_error = self.on_auth_error, None
        try:
            self.list_snapshots(1, project_id)
        finally:
            self.on_auth_error = ask

    def token_seconds_left(self) -> Optional[float]:
        """Seconds until the token expires, if it is a JWT with an expiry."""
        if self.token_expires_at is None:
            return None
        return (self.token_expires_at - datetime.now(timezone.utc)).total_seconds()

    # -- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """`method` {base_url}{path}; the parsed JSON body ({} when empty).
        On HTTP 401/403 asks on_auth_error for a new token and repeats the
        request with it, as long as one is given; raises SnapshotAuthError
        when none is. Raises requests.HTTPError (with the body) on another
        error status."""
        url = self.base_url + path
        replaced = None
        while True:
            resp = self.session.request(method, url, timeout=self.timeout_s, **kwargs)
            if resp.status_code not in (401, 403):
                break
            left = self.token_seconds_left()
            problem = "%s %s -> HTTP %d: token rejected%s. Reply: %s" % (
                method, url, resp.status_code,
                " (it expired %.0f min ago)" % (-left / 60.0) if left is not None and left <= 0 else "",
                resp.text[:300])
            log_step("Snapshot API: %s", problem)
            new_token = self.on_auth_error(problem) if self.on_auth_error else None
            if not new_token:
                raise SnapshotAuthError(problem + " Put a new TOKEN in .env.")
            try:
                self.set_token(new_token)
                replaced = clean_token(new_token)
                log_step("Snapshot API: repeating %s %s with the new token", method, url)
            except SnapshotAuthError as exc:
                log_step("Snapshot API: the new token is not usable: %s", exc)
        if replaced and self.on_new_token:
            self.on_new_token(replaced)
        if not resp.ok:
            raise requests.HTTPError("%s %s -> HTTP %d: %s" % (method, url, resp.status_code, resp.text[:500]),
                                     response=resp)
        return resp.json() if resp.content else {}

    # -- API 1 / 2 / 3 ------------------------------------------------------

    def trigger_snapshot(self, project_id: str, name: str = SNAPSHOT_NAME, comment: str = SNAPSHOT_COMMENT,
                         is_major: bool = SNAPSHOT_IS_MAJOR) -> str:
        """API 1: POST /api/v1/projects/<project_id>/snapshots
        {"name", "comment", "is_major"}; returns the new snapshot's id.
        Sets `triggered_at` just before the request is sent."""
        payload = {"name": name, "comment": comment, "is_major": is_major}
        log_step("Triggering snapshot of project %s: %s", project_id, json.dumps(payload))
        self.polls = 0
        self.poll_history = []
        self.triggered_at = time.monotonic()
        body = self._request(TRIGGER_METHOD, TRIGGER_PATH.format(project_id=project_id), json=payload)
        log_step("Trigger response: %s", json.dumps(body)[:1000])
        self.project_id = str(project_id)
        self.trigger_response = body
        snapshot_id = snapshot_id_from(body)
        if snapshot_id is None:
            raise SnapshotError("no snapshot id (%s) in the trigger response - set SNAPSHOT_ID_FIELD to the "
                                "field that holds it: %r" % (SNAPSHOT_ID_FIELD or "/".join(SNAPSHOT_ID_FIELDS), body))
        # e.g. {"job_id": 2389, "project_id": 223, "name": "...", "state": "queued",
        #       "status_url": "/api/v1/projects/223/snapshot-jobs/2389"}
        problems = []
        got_project = field(body, PROJECT_ID_FIELDS)
        if got_project is not None and str(got_project) != str(project_id):
            problems.append("project_id %r, expected %r" % (got_project, project_id))
        got_name = field(body, NAME_FIELDS)
        if got_name is not None and str(got_name) != str(name):
            problems.append("name %r, expected %r" % (got_name, name))
        if problems:
            raise SnapshotError("trigger response does not match the request: %s. Response: %r"
                                % ("; ".join(problems), body))
        status_url = field(body, STATUS_URL_FIELDS)
        if status_url:
            status_url = str(status_url)
            if status_url.startswith(self.base_url + "/"):
                status_url = status_url[len(self.base_url):]
            if status_url.startswith("/"):
                self.status_urls[str(snapshot_id)] = status_url
            else:  # another host: never send the token there; poll STATUS_PATH instead
                log_step("Ignoring status_url %s (not under API_BASE_URL)", status_url)
                status_url = None
        log_step("Snapshot job id %s (from the trigger response), state %s, status url %s", snapshot_id,
                 field(body, STATUS_FIELDS), status_url or "-")
        return str(snapshot_id)

    def _path(self, template: str, snapshot_id: str, project_id: Optional[str]) -> str:
        """`template` with {project_id} (default: the one trigger_snapshot()
        was given) and {snapshot_id} filled in."""
        project_id = project_id or self.project_id
        if project_id is None:
            raise SnapshotError("no project id: pass project_id or call trigger_snapshot() first")
        return template.format(project_id=project_id, snapshot_id=snapshot_id)

    def get_status(self, snapshot_id: str, project_id: Optional[str] = None) -> str:
        """API 2: GET /api/v1/projects/<project_id>/snapshot-jobs/<snapshot_id>;
        the snapshot's status, in lower case. The whole reply is kept in
        `last_status`."""
        # the trigger response's status_url, when it gave one for this job and project
        url = self.status_urls.get(str(snapshot_id)) if project_id in (None, self.project_id) else None
        body = self._request("GET", url or self._path(STATUS_PATH, snapshot_id, project_id))
        self.last_status = body
        status = field(body, STATUS_FIELDS)
        if status is None:
            raise SnapshotError("no status (%s) in the status response: %r" % ("/".join(STATUS_FIELDS), body))
        return str(status).strip().lower()

    def snapshot_timing(self, snapshot_id: str) -> dict:
        """The snapshot time from the status response of the successful
        poll (`last_status`): {"started_at", "ended_at" (as the API gives
        them), "duration_s" (end - start)}. If that response has no end
        time yet, the status is read once more. Raises SnapshotError when
        the times are missing, unreadable, or end before they start."""
        body = self.last_status or {}
        start, end = snapshot_times(body)
        if start is None or end is None:
            log_step("Snapshot %s: start/end time not in the status response yet - reading it again", snapshot_id)
            self.get_status(snapshot_id)
            body = self.last_status or {}
            start, end = snapshot_times(body)
        duration = duration_s(start, end)
        if duration is None:
            raise SnapshotError(
                "cannot read the snapshot time from the status response: start %r (%s), end %r (%s). Set "
                "SNAPSHOT_START_FIELD / SNAPSHOT_END_FIELD to the fields that hold them. Response: %r"
                % (start, SNAPSHOT_START_FIELD or "/".join(STARTED_FIELDS), end,
                   SNAPSHOT_END_FIELD or "/".join(ENDED_FIELDS), body))
        if duration < 0:
            raise SnapshotError("snapshot %s ends (%s) before it starts (%s)" % (snapshot_id, end, start))
        log_step("Snapshot %s time (status response): %.1fs, %s -> %s", snapshot_id, duration, start, end)
        return {"started_at": start, "ended_at": end, "duration_s": duration}

    def list_snapshots(self, page: int = 1, project_id: Optional[str] = None) -> list:
        """API 3: one page of the project's snapshots (newest first), as a
        list of dicts."""
        body = self._request("GET", self._path(LIST_PATH, "", project_id),
                             params={"page": page, "page_size": LIST_PAGE_SIZE, "sort": LIST_SORT})
        self.last_list = body if isinstance(body, dict) else None
        items = list_items(body)
        if items is None:
            raise SnapshotError("no list of snapshots (%s) in the snapshot list response: %r"
                                % ("/".join(LIST_ITEMS_FIELDS), body)[:1500])
        return items

    def find_listed_snapshot(self, name: str, project_id: Optional[str] = None,
                             listed_id=None) -> dict:
        """Our snapshot's entry in the snapshot list: the one with snapshot_id
        `listed_id` when given (from API 2's "snapshot"), else the one named
        `name` (unique per run). Reads up to LIST_MAX_PAGES pages (stopping
        at the response's "total"), up to LIST_ATTEMPTS times in case the
        list lags behind the status. Raises SnapshotError if not listed."""
        if listed_id is not None:
            def matches(item):
                return str(field(item, LISTED_SNAPSHOT_ID_FIELDS)) == str(listed_id)
            wanted = "snapshot_id %s" % listed_id
        else:
            def matches(item):
                return str(field(item, NAME_FIELDS)) == name
            wanted = "name %r" % name
        seen = []
        for attempt in range(1, LIST_ATTEMPTS + 1):
            seen = []
            for page in range(1, LIST_MAX_PAGES + 1):
                items = self.list_snapshots(page, project_id)
                for item in items:
                    if matches(item):
                        log_step("Snapshot with %s found in the snapshot list (page %d): %s",
                                 wanted, page, json.dumps(item)[:1000])
                        return item
                    seen.append("%s:%s" % (field(item, LISTED_SNAPSHOT_ID_FIELDS), field(item, NAME_FIELDS)))
                total = field(self.last_list or {}, ("total", "count", "total_count"))
                if len(items) < LIST_PAGE_SIZE or (isinstance(total, int) and len(seen) >= total):
                    break  # last page
            if attempt < LIST_ATTEMPTS:
                log_step("Snapshot with %s not in the snapshot list yet (read %d/%d) - again in %gs",
                         wanted, attempt, LIST_ATTEMPTS, LIST_RETRY_S)
                time.sleep(LIST_RETRY_S)
        raise SnapshotError("snapshot with %s is not in the snapshot list of project %s (%d entries read, "
                            "id:name %s)" % (wanted, project_id or self.project_id, len(seen), seen[:20]))

    def get_details(self, snapshot_id: str, name: str = SNAPSHOT_NAME, project_id: Optional[str] = None,
                    listed_id=None) -> dict:
        """API 3: our snapshot's entry in the snapshot list (see
        find_listed_snapshot()): by `listed_id` (the created snapshot's id)
        when known, else by `name`. `snapshot_id` (API 1's job id) is only
        logged."""
        log_step("Looking for the snapshot of job %s (snapshot_id %s, name %r) in the snapshot list",
                 snapshot_id, listed_id, name)
        return self.find_listed_snapshot(name, project_id, listed_id)

    # -- polling ------------------------------------------------------------

    def wait_until_complete(self, snapshot_id: str, interval_s: float = 10, timeout_s: float = 7200,
                            started: Optional[float] = None) -> float:
        """Poll get_status() every `interval_s` until the snapshot succeeds;
        returns the seconds from `started` (default: `triggered_at`) to the
        poll that saw success - so the time is accurate to one interval.

        Raises SnapshotFailed at once on a failed/error/cancelled status,
        SnapshotTimeout when not successful `timeout_s` after `started`, and
        SnapshotError after MAX_POLL_ERRORS failed requests in a row."""
        started = started if started is not None else self.triggered_at
        if started is None:
            started = time.monotonic()
        deadline = started + timeout_s
        status, errors, last_error = None, 0, ""
        while True:
            try:
                status = self.get_status(snapshot_id)
                errors = 0
            except requests.RequestException as exc:
                errors += 1
                last_error = str(exc).splitlines()[0][:200] if str(exc) else repr(exc)
                log_step("Snapshot %s: status request failed (%d/%d): %s",
                         snapshot_id, errors, MAX_POLL_ERRORS, exc)
                if errors >= MAX_POLL_ERRORS:
                    raise SnapshotError("snapshot %s: %d status requests failed in a row, last: %s"
                                        % (snapshot_id, errors, exc)) from exc
            self.polls += 1
            elapsed = time.monotonic() - started
            body = self.last_status or {}
            entry = {"poll": self.polls, "elapsed_s": round(elapsed, 1),
                     "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            if errors:
                entry["error"] = last_error
            else:
                entry.update(state=status, step=field(body, STEP_FIELDS), step_number=field(body, STEP_NUMBER_FIELDS),
                             step_count=field(body, STEP_COUNT_FIELDS))
            self.poll_history.append(entry)
            if status is not None and errors == 0:
                log_step("Snapshot %s: %s%s after %.1fs (poll %d)", snapshot_id, status, step_text(body),
                         elapsed, self.polls)
            if status in SUCCESS_STATUSES:
                return elapsed
            if status in FAILED_STATUSES:
                raise SnapshotFailed("snapshot %s ended with status %r%s after %.1fs: error %r"
                                     % (snapshot_id, status, step_text(body), elapsed,
                                        field(body, ERROR_FIELDS)))
            if status is not None and errors == 0 and field(body, ENDED_FIELDS) is not None:
                # finished, but with a state not listed above: stop now instead of polling to the timeout
                raise SnapshotError("snapshot %s finished (%s) with unknown state %r, error %r - add it to "
                                    "SUCCESS_STATUSES or FAILED_STATUSES in common/snapshot_api.py"
                                    % (snapshot_id, field(body, ENDED_FIELDS), status, field(body, ERROR_FIELDS)))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SnapshotTimeout("snapshot %s not successful within %gs (%.1f min): last status %r after "
                                      "%d polls" % (snapshot_id, timeout_s, timeout_s / 60.0, status, self.polls))
            time.sleep(min(interval_s, remaining))


# -- details -----------------------------------------------------------------


def step_text(body: dict) -> str:
    """API 2's progress as ", step cloning (2/6)"; "" when it has none."""
    step = field(body, STEP_FIELDS)
    number, count = field(body, STEP_NUMBER_FIELDS), field(body, STEP_COUNT_FIELDS)
    if step is None and number is None:
        return ""
    where = " (%s/%s)" % (number, count) if number is not None else ""
    return ", step %s%s" % (step if step is not None else "", where)


def created_snapshot_id(status_body: dict):
    """The id of the snapshot a finished job created: API 2's "snapshot"
    (an object with snapshot_id/id, or the id itself), else a top-level
    snapshot_id; None when it has none."""
    snap = field(status_body, SNAPSHOT_OBJECT_FIELDS)
    if isinstance(snap, dict):
        found = field(snap, LISTED_SNAPSHOT_ID_FIELDS + ("id",))
        if found is not None:
            return found
    elif isinstance(snap, (int, str)) and not isinstance(snap, bool):
        return snap
    return field(status_body, LISTED_SNAPSHOT_ID_FIELDS)


def step_durations(poll_history: list) -> dict:
    """{step name: seconds} from a poll history: each step lasts from the
    first poll that showed it to the first poll that showed the next one -
    so each is accurate to one poll interval. The last step shown (the one
    the snapshot ended in) gets no time."""
    seen = []  # (step, first elapsed_s)
    for entry in poll_history or []:
        step = entry.get("step")
        if step and (not seen or seen[-1][0] != step):
            seen.append((step, entry.get("elapsed_s") or 0.0))
    return {step: round(seen[i + 1][1] - at, 1) for i, (step, at) in enumerate(seen[:-1])}


def queue_wait_s(status_body: dict) -> Optional[float]:
    """Seconds API 2's job waited before it started: started_at - requested_at."""
    return duration_s((status_body or {}).get("requested_at"), field(status_body or {}, STARTED_FIELDS))


def _parse_time(value) -> Optional[datetime]:
    """An ISO 8601 string (Z allowed) or epoch seconds/milliseconds as a
    datetime; None if it is neither."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0 if value > 1e11 else value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_s(start, end) -> Optional[float]:
    """Seconds from `start` to `end` (ISO 8601 strings or epoch numbers),
    or None when either is missing or unreadable, or only one has a time
    zone."""
    start, end = _parse_time(start), _parse_time(end)
    if start is None or end is None or (start.tzinfo is None) != (end.tzinfo is None):
        return None
    return round((end - start).total_seconds(), 3)


# -- results -------------------------------------------------------------------


def result_file_name(result: dict) -> str:
    """snapshot_time_<scenario>_<size>gb.json (e.g. snapshot_time_add_2gb.json);
    without a size snapshot_time_<scenario>.json, without a scenario snapshot_time.json."""
    if not result.get("scenario"):
        return "snapshot_time.json"
    size = result.get("target_gb")
    return "snapshot_time_%s%s.json" % (result["scenario"], "_%sgb" % ("%g" % float(size)).replace(".", "p")
                                        if size not in (None, "") else "")


def record_snapshot_result(result: dict) -> str:
    """Log `result`, write it as snapshot_time_<scenario>.json in this test file's
    evidence folder (attached to Allure), and append it to the shared
    evidence/snapshot_times.csv (one row per run, for comparing runs).
    Returns the JSON file's path."""
    log_step("Snapshot result: %s", json.dumps(result))
    # one file per scenario and size: several of them share one run's evidence folder
    json_path = os.path.join(evidence_dir(), result_file_name(result))
    write_json_report(json_path, result)
    attach_allure_file(json_path)

    _upgrade_results_csv()
    new_file = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        steps = result.get("step_durations_s") or {}
        writer.writerow(dict(result, run_stamp=RUN_STAMP, **{s + "_s": steps.get(s) for s in CRITICAL_STEPS}))
    attach_allure_file(RESULTS_CSV)
    log_step("Snapshot result written to %s and %s", json_path, RESULTS_CSV)
    try:  # the tabular report of all runs; a report problem must not fail the run
        from common.snapshot_report import write_report
        html_path, md_path, png_path = write_report()
        attach_allure_file(html_path)
        if png_path:
            attach_allure_file(png_path)
        log_step("Snapshot report updated: %s (and %s%s)", html_path, md_path,
                 ", %s" % png_path if png_path else "; no PNG: matplotlib is not installed")
    except Exception as exc:  # noqa: BLE001
        log_step("WARNING: could not write the snapshot report: %s", exc)
    return json_path


def _upgrade_results_csv():
    """Rewrite RESULTS_CSV with today's RESULT_FIELDS header when it was
    written with an older one, keeping its rows (a column it lacks is left
    empty; rows from before the scenario column were add runs; the critical
    step times and the cycle's data size, target_gb, are filled in from the
    run's saved JSON and its add / remove rows)."""
    if not os.path.exists(RESULTS_CSV):
        return
    with open(RESULTS_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) == RESULT_FIELDS:
            return
        old_header = "scenario" not in (reader.fieldnames or ())
        rows = list(reader)
    # the data size of each run's cycle: what its add made or its remove deleted
    cycle_gb = {}
    for row in rows:
        if row.get("data_gb") and (row.get("data_action") or row.get("scenario") or "add") in ("add", "remove"):
            cycle_gb.setdefault(row.get("run_stamp"), row["data_gb"])
    for row in rows:
        if old_header:
            row.setdefault("scenario", "add")
            row.setdefault("data_action", "add")
        if not row.get("test"):
            row["test"] = "test_project_snapshot_time"  # the only snapshot test before the column
        if not row.get("list_check") and row.get("status") in SUCCESS_STATUSES:
            row["list_check"] = "passed"  # rows were only written after the list check passed
        saved = _saved_result(row)
        if not any(row.get(s + "_s") for s in CRITICAL_STEPS):
            steps = saved.get("step_durations_s") or step_durations(saved.get("poll_history"))
            row.update({s + "_s": steps.get(s) for s in CRITICAL_STEPS})
        if not row.get("target_gb"):
            size = saved.get("target_gb") or cycle_gb.get(row.get("run_stamp"))
            if not size and _num(row.get("data_gb")) and _num(row.get("data_percent")):
                # a modify run on its own: the folder's size = the bytes changed / the share changed
                size = _num(row["data_gb"]) * 100.0 / _num(row["data_percent"])
            row["target_gb"] = "%g" % round(float(size), 1) if size else ""
    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log_step("Upgraded %s to the columns %s (%d row(s) kept)", RESULTS_CSV, ", ".join(RESULT_FIELDS), len(rows))


def _num(value) -> Optional[float]:
    """`value` as a float; None when it is empty or not a number."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _saved_result(row: dict) -> dict:
    """A CSV row's saved snapshot_time*.json (matched by run stamp and job
    id); {} when there is none."""
    import glob

    pattern = os.path.join(EVIDENCE_ROOT, "runs", "%s_*" % row.get("run_stamp", ""), "snapshot_time*.json")
    for path in glob.glob(pattern):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if str(data.get("snapshot_id")) == str(row.get("snapshot_id")):
            return data
    return {}
