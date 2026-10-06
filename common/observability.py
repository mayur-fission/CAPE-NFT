"""Optional local observability stack, run by hand rather than by the tests:
Prometheus scrapes the OTel exporter from common/otel_metrics.py and Grafana
graphs it on a provisioned dashboard. Call
otel_metrics.enable_prometheus_metrics_export() and record some metrics,
then start_observability_stack().

Starts real local processes; expects `prometheus` and `grafana` on PATH.
"""
import os
import subprocess
import time
import webbrowser

import requests
import yaml

from common.config import EVIDENCE_DIR

DEFAULT_PROMETHEUS_CONFIG_PATH = os.path.join(EVIDENCE_DIR, "prometheus.yml")
DEFAULT_PROMETHEUS_DATA_DIR = os.path.join(EVIDENCE_DIR, "prometheus-data")
PROMETHEUS_LOG_PATH = os.path.join(EVIDENCE_DIR, "prometheus.log")
GRAFANA_LOG_PATH = os.path.join(EVIDENCE_DIR, "grafana.log")
DASHBOARD_TITLE = "RStudio Session Launch Performance"


def _popen_logging_to(cmd, log_path):
    """Start `cmd` with its output appended to `log_path`. A file, not a
    PIPE: nothing reads a pipe, and a long-running server blocks on write
    once the pipe buffer fills."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "ab") as log:
        return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)


# -- Prometheus ---------------------------------------------------------

def write_prometheus_scrape_config(path=None, targets=("localhost:9464",), scrape_interval="5s"):
    """Write a prometheus.yml scraping `targets` and return its path."""
    path = path or DEFAULT_PROMETHEUS_CONFIG_PATH
    config = {
        "global": {"scrape_interval": scrape_interval},
        "scrape_configs": [
            {
                "job_name": "rstudio_session_perf",
                "static_configs": [{"targets": list(targets)}],
            }
        ],
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False)
    return path


def start_prometheus_server(config_path=None, port=9090, binary="prometheus", startup_timeout_s=20):
    """Start Prometheus in the background and wait until it's healthy.

    Returns the Popen handle (stop it with stop_process()), or None if a
    healthy Prometheus was already running on `port`. Raises RuntimeError if
    the binary is missing or it never becomes healthy.
    """
    url = "http://localhost:%d/-/healthy" % port
    try:
        if requests.get(url, timeout=2).ok:
            print("[rstudio-local] Prometheus already running and healthy on port %d - reusing it" % port)
            return None
    except requests.RequestException:
        pass

    config_path = config_path or DEFAULT_PROMETHEUS_CONFIG_PATH
    os.makedirs(DEFAULT_PROMETHEUS_DATA_DIR, exist_ok=True)

    try:
        proc = _popen_logging_to(
            [
                binary,
                "--config.file=%s" % config_path,
                "--storage.tsdb.path=%s" % DEFAULT_PROMETHEUS_DATA_DIR,
                "--web.listen-address=:%d" % port,
            ],
            PROMETHEUS_LOG_PATH,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "'%s' not found on PATH. Install it (e.g. `scoop install prometheus`) "
            "or pass binary=<full path>." % binary
        )

    deadline = time.time() + startup_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "prometheus exited early (code %s) - check its config at %s and its log at %s"
                % (proc.returncode, config_path, PROMETHEUS_LOG_PATH)
            )
        try:
            if requests.get(url, timeout=2).ok:
                return proc
        except requests.RequestException:
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError("prometheus did not become healthy within %ds" % startup_timeout_s)


# -- Grafana --------------------------------------------------------------

def start_grafana_server(binary="grafana", port=3000, homepath=None, startup_timeout_s=30):
    """Start Grafana in the background and wait until it's healthy.

    `homepath` is Grafana's install root; defaults to the scoop install.
    Returns the Popen handle, or None if Grafana was already running on
    `port`. Raises RuntimeError if the binary is missing or it never becomes
    healthy.
    """
    url = "http://localhost:%d/api/health" % port
    try:
        if requests.get(url, timeout=2).ok:
            print("[rstudio-local] Grafana already running and healthy on port %d - reusing it" % port)
            return None
    except requests.RequestException:
        pass

    if homepath is None:
        homepath = _guess_scoop_grafana_homepath()

    cmd = [binary, "server", "--homepath=%s" % homepath] if homepath else [binary, "server"]
    try:
        proc = _popen_logging_to(cmd, GRAFANA_LOG_PATH)
    except FileNotFoundError:
        raise RuntimeError(
            "'%s' not found on PATH. Install it (e.g. `scoop bucket add extras && "
            "scoop install grafana`) or pass binary=<full path>." % binary
        )

    deadline = time.time() + startup_timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "grafana exited early (code %s) - check its log at %s" % (proc.returncode, GRAFANA_LOG_PATH)
            )
        try:
            if requests.get(url, timeout=2).ok:
                return proc
        except requests.RequestException:
            pass
        time.sleep(0.5)

    proc.terminate()
    raise RuntimeError("grafana did not become healthy within %ds" % startup_timeout_s)


def _guess_scoop_grafana_homepath():
    """Grafana's scoop install root, if it exists."""
    candidate = os.path.expanduser(r"~\scoop\apps\grafana\current")
    return candidate if os.path.isdir(candidate) else None


def stop_process(proc):
    """Stop a process returned by start_prometheus_server()/start_grafana_server()."""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def configure_grafana_datasource(prometheus_url="http://localhost:9090", grafana_url="http://localhost:3000",
                                  auth=("admin", "admin"), name="Prometheus"):
    """Register (or update) a Prometheus datasource in Grafana pointing at
    `prometheus_url` and return its uid. Reuses an existing one with the
    same URL, including a read-only provisioned one.
    """
    payload = {
        "name": name,
        "type": "prometheus",
        "url": prometheus_url,
        "access": "proxy",
        "isDefault": True,
    }

    existing = requests.get("%s/api/datasources/name/%s" % (grafana_url, name), auth=auth, timeout=10)
    if existing.status_code == 200:
        data = existing.json()
        if data.get("url") == prometheus_url:
            return data["uid"]
        if data.get("readOnly"):
            raise RuntimeError(
                "a '%s' datasource already exists in Grafana but is file-provisioned "
                "(read-only) and points at %s, not %s - edit Grafana's own "
                "provisioning config (conf/provisioning/datasources/) to change it, "
                "or pass a different `name`" % (name, data.get("url"), prometheus_url)
            )
        resp = requests.put(
            "%s/api/datasources/%s" % (grafana_url, data["id"]), json=payload, auth=auth, timeout=10
        )
    else:
        resp = requests.post("%s/api/datasources" % grafana_url, json=payload, auth=auth, timeout=10)
    resp.raise_for_status()
    return resp.json()["datasource"]["uid"]


def create_launch_performance_dashboard(datasource_uid, grafana_url="http://localhost:3000",
                                         auth=("admin", "admin")):
    """Create the Grafana dashboard for the launch metrics recorded by
    capture_otel_metrics() and return its URL.
    """
    def _panel(panel_id, title, expr, unit, grid_pos):
        return {
            "id": panel_id,
            "title": title,
            "type": "timeseries",
            "datasource": {"type": "prometheus", "uid": datasource_uid},
            "fieldConfig": {"defaults": {"unit": unit}, "overrides": []},
            "gridPos": grid_pos,
            "targets": [{"expr": expr, "refId": "A", "datasource": {"type": "prometheus", "uid": datasource_uid}}],
        }

    dashboard = {
        "dashboard": {
            "title": DASHBOARD_TITLE,
            "uid": "rstudio-session-launch-perf",
            "timezone": "browser",
            "refresh": "10s",
            "panels": [
                # Names as exported via the OTel Collector (the default).
                # The direct Prometheus exporter names bytes_received
                # "..._bytes_received_bytes" instead - check /metrics if
                # these drift.
                _panel(
                    1, "Launch duration (p95)",
                    "histogram_quantile(0.95, sum(rate(rstudio_session_launch_duration_seconds_bucket[5m])) by (le))",
                    "s", {"x": 0, "y": 0, "w": 12, "h": 8},
                ),
                _panel(
                    2, "Bytes received per launch (p95)",
                    "histogram_quantile(0.95, sum(rate(rstudio_session_launch_bytes_received_bucket[5m])) by (le))",
                    "bytes", {"x": 12, "y": 0, "w": 12, "h": 8},
                ),
                _panel(
                    3, "Throughput (sessions/sec)",
                    "rstudio_session_launch_throughput_per_second_sum",
                    "short", {"x": 0, "y": 8, "w": 12, "h": 8},
                ),
                _panel(
                    4, "Launch attempts by status",
                    "sum(rstudio_session_launch_count_total) by (status)",
                    "short", {"x": 12, "y": 8, "w": 12, "h": 8},
                ),
            ],
        },
        "overwrite": True,
    }

    resp = requests.post("%s/api/dashboards/db" % grafana_url, json=dashboard, auth=auth, timeout=10)
    resp.raise_for_status()
    return grafana_url + resp.json()["url"]


def open_dashboard(dashboard_url):
    """Open `dashboard_url` in the default browser."""
    return webbrowser.open(dashboard_url)


def start_observability_stack(prometheus_targets=("localhost:9464",), grafana_auth=("admin", "admin")):
    """Start Prometheus and Grafana, link them, create the dashboard and open
    it. Call enable_prometheus_metrics_export() and record some metrics first.

    Returns (prometheus_proc, grafana_proc, dashboard_url).
    """
    config_path = write_prometheus_scrape_config(targets=prometheus_targets)
    prometheus_proc = start_prometheus_server(config_path=config_path)
    grafana_proc = start_grafana_server()

    # Prometheus needs at least one scrape to have happened before its data
    # is queryable from Grafana.
    time.sleep(6)

    datasource_uid = configure_grafana_datasource(auth=grafana_auth)
    dashboard_url = create_launch_performance_dashboard(datasource_uid, auth=grafana_auth)
    open_dashboard(dashboard_url)

    return prometheus_proc, grafana_proc, dashboard_url
