"""PMT API client.

This is the one file that needs real work. Keep the method names and the shape
of what they return, because the Locust users and the pytest suites both depend
on them. Change the URL paths and payloads to match your API.

Every method returns a plain dict, or raises requests.HTTPError.
"""
import time
import requests

from common.auth import session_for, base_url, verify_tls


class PMTClient:
    def __init__(self, role="user", session=None):
        self.role = role
        self.s = session or session_for(role)
        self.base = base_url()

    # -- helpers -------------------------------------------------------
    def _url(self, path):
        return self.base + path

    def _json(self, resp):
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # -- projects ------------------------------------------------------
    def open_project(self, project_id):
        """AC4, AC7. Returns the full project state the user would see."""
        return self._json(self.s.get(self._url("/api/projects/%s" % project_id), timeout=60))

    def list_files(self, project_id):
        """AC6. Metadata-heavy at 150k files - this is the one that hurts."""
        return self._json(
            self.s.get(self._url("/api/projects/%s/files" % project_id), timeout=1800)
        )

    # -- writes (AC3 - one method per access method) --------------------
    def save_file_api(self, project_id, path, content):
        return self._json(
            self.s.put(
                self._url("/api/projects/%s/files/%s" % (project_id, path)),
                data=content,
                timeout=300,
            )
        )

    def read_file_api(self, project_id, path):
        r = self.s.get(
            self._url("/api/projects/%s/files/%s" % (project_id, path)), timeout=300
        )
        r.raise_for_status()
        return r.content

    # -- WIP sessions ---------------------------------------------------
    def open_wip_session(self, project_id):
        """AC7. Must remain functional for the whole soak."""
        return self._json(
            self.s.post(self._url("/api/projects/%s/wip/session" % project_id), timeout=60)
        )

    def wip_heartbeat(self, session_id):
        return self._json(
            self.s.post(self._url("/api/wip/session/%s/heartbeat" % session_id), timeout=30)
        )

    def close_wip_session(self, session_id):
        return self._json(
            self.s.delete(self._url("/api/wip/session/%s" % session_id), timeout=30)
        )

    # -- non-WIP actions (AC6) -----------------------------------------
    def start_action(self, project_id, action):
        return self._json(
            self.s.post(
                self._url("/api/projects/%s/actions" % project_id),
                json={"action": action},
                timeout=120,
            )
        )

    def get_action(self, action_id):
        return self._json(self._get_or_raise("/api/actions/%s" % action_id))

    def _get_or_raise(self, path, timeout=60):
        return self.s.get(self._url(path), timeout=timeout)

    def wait_for_action(self, action_id, timeout_s, poll_s=30):
        """Poll until terminal. Returns (state_dict, elapsed_seconds)."""
        start = time.time()
        while time.time() - start < timeout_s:
            state = self.get_action(action_id)
            if state.get("status") in ("completed", "failed", "cancelled"):
                return state, time.time() - start
            time.sleep(poll_s)
        return {"status": "timeout"}, time.time() - start

    # -- health (AC12) --------------------------------------------------
    def health_details(self, authenticated=True):
        """Returns (status_code, body). Unauthenticated call must not leak."""
        if authenticated:
            r = self.s.get(self._url("/health/details"), timeout=30)
        else:
            r = requests.get(
                self._url("/health/details"), timeout=30, verify=verify_tls()
            )
        body = {}
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:2000]}
        return r.status_code, body

    # -- backup and restore (AC10, AC11) --------------------------------
    def list_backups(self):
        return self._json(self.s.get(self._url("/api/backups"), timeout=60))

    def latest_backup(self):
        items = self.list_backups().get("items", [])
        return items[0] if items else None

    def create_restore(self, restore_type, source, target):
        """restore_type: full | incremental | point_in_time"""
        return self._json(
            self.s.post(
                self._url("/api/restores"),
                json={"type": restore_type, "source": source, "target": target},
                timeout=120,
            )
        )

    def get_restore(self, restore_id):
        return self._json(self.s.get(self._url("/api/restores/%s" % restore_id), timeout=60))

    def wait_for_restore(self, restore_id, timeout_s, poll_s=60):
        start = time.time()
        while time.time() - start < timeout_s:
            state = self.get_restore(restore_id)
            if state.get("status") in ("completed", "failed"):
                return state, time.time() - start
            time.sleep(poll_s)
        return {"status": "timeout"}, time.time() - start

    # -- audit and permissions (AC5) ------------------------------------
    def audit_entries(self, since=None, limit=100):
        params = {"limit": limit}
        if since:
            params["since"] = since
        return self._json(self.s.get(self._url("/api/audit"), params=params, timeout=60))

    def attempt(self, method, path, **kwargs):
        """Raw attempt that does NOT raise. For negative authorisation tests."""
        kwargs.setdefault("timeout", 60)
        return self.s.request(method, self._url(path), **kwargs)
