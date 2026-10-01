"""Metrics backend.

CloudWatch is the default. The Prometheus backend is kept because the interface
is identical, so moving to Amazon Managed Prometheus later costs nothing in the
test code - set METRICS_BACKEND=prometheus and nothing else changes.

Both classes expose: scalar(), p95_latency(), error_rate_pct(),
max_clock_offset(), up_targets(). Tests only ever call those.
"""
import datetime
import time

from common.config import env

NAMESPACE = "PMT/NFR"


# --------------------------------------------------------------- CloudWatch
class CloudWatch:
    """CloudWatch metrics via GetMetricData.

    - Resolution: standard metrics are 1-minute, too coarse for a 60s
      window, so high_res defaults to True (period=1).
    - Latency: custom metrics can take ~2 minutes to become queryable; use
      settle() before reading one you just wrote.
    """

    def __init__(self, region=None, namespace=NAMESPACE):
        import boto3

        self.region = region or env("AWS_REGION", "eu-west-1")
        self.namespace = namespace
        self.cw = boto3.client("cloudwatch", region_name=self.region)
        self.logs = boto3.client("logs", region_name=self.region)

    # -- core ------------------------------------------------------------
    def get_metric(
        self,
        metric_name,
        dimensions=None,
        stat="Average",
        minutes=5,
        namespace=None,
        high_res=True,
    ):
        """Return the most recent datapoint, or None when there is no data."""
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(minutes=minutes)
        dims = [{"Name": k, "Value": v} for k, v in (dimensions or {}).items()]

        resp = self.cw.get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "q1",
                    "MetricStat": {
                        "Metric": {
                            "Namespace": namespace or self.namespace,
                            "MetricName": metric_name,
                            "Dimensions": dims,
                        },
                        "Period": 1 if high_res else 60,
                        "Stat": stat,
                    },
                    "ReturnData": True,
                }
            ],
            StartTime=start,
            EndTime=end,
            ScanBy="TimestampDescending",
        )
        values = resp["MetricDataResults"][0]["Values"]
        return float(values[0]) if values else None

    def insights(self, expression, minutes=5):
        """Metric Insights (SQL) query. Returns list of (label, latest value)."""
        end = datetime.datetime.now(datetime.timezone.utc)
        start = end - datetime.timedelta(minutes=minutes)
        resp = self.cw.get_metric_data(
            MetricDataQueries=[
                {"Id": "q1", "Expression": expression, "Period": 60, "ReturnData": True}
            ],
            StartTime=start,
            EndTime=end,
            ScanBy="TimestampDescending",
        )
        out = []
        for r in resp["MetricDataResults"]:
            if r["Values"]:
                out.append((r.get("Label", r["Id"]), float(r["Values"][0])))
        return out

    def logs_insights(self, log_group, query, minutes=15, timeout_s=120):
        """CloudWatch Logs Insights. Used for audit-trail verification (AC5)."""
        end = int(time.time())
        start = end - minutes * 60
        qid = self.logs.start_query(
            logGroupName=log_group, startTime=start, endTime=end, queryString=query
        )["queryId"]

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            res = self.logs.get_query_results(queryId=qid)
            if res["status"] in ("Complete", "Failed", "Cancelled", "Timeout", "Unknown"):
                if res["status"] != "Complete":
                    raise RuntimeError("Logs Insights query %s" % res["status"])
                return [
                    {f["field"]: f["value"] for f in row} for row in res["results"]
                ]
            time.sleep(2)
        raise TimeoutError("Logs Insights query did not finish in %ss" % timeout_s)

    def alarm_state(self, alarm_name):
        """OK | ALARM | INSUFFICIENT_DATA. Several negative tests assert ALARM."""
        # describe_alarms returns only metric alarms unless AlarmTypes asks for both.
        resp = self.cw.describe_alarms(
            AlarmNames=[alarm_name], AlarmTypes=["MetricAlarm", "CompositeAlarm"]
        )
        alarms = resp.get("MetricAlarms", []) + resp.get("CompositeAlarms", [])
        if not alarms:
            raise LookupError("no CloudWatch alarm named %r" % alarm_name)
        return alarms[0]["StateValue"]

    def alarms_in_state(self, state="ALARM", prefix=None):
        kw = {"StateValue": state, "AlarmTypes": ["MetricAlarm", "CompositeAlarm"]}
        if prefix:
            kw["AlarmNamePrefix"] = prefix
        names = []
        for page in self.cw.get_paginator("describe_alarms").paginate(**kw):
            for a in page.get("MetricAlarms", []) + page.get("CompositeAlarms", []):
                names.append(a["AlarmName"])
        return names

    @staticmethod
    def settle(seconds=150):
        """Wait for custom metrics to become queryable. See note in class docstring."""
        time.sleep(seconds)

    # -- interface shared with the Prometheus backend ---------------------
    def scalar(self, metric_name, default=None, **kw):
        v = self.get_metric(metric_name, **kw)
        return default if v is None else v

    def p95_latency(self, service, minutes=5):
        """Requires the p95 to be published as a metric, or use ALB TargetResponseTime."""
        v = self.get_metric(
            "RequestLatencyP95", dimensions={"Service": service}, minutes=minutes
        )
        if v is not None:
            return v
        # fall back to the load balancer's own view
        return self.get_metric(
            "TargetResponseTime",
            dimensions={"LoadBalancer": env("PMT_ALB_DIMENSION", "")},
            stat="p95",
            namespace="AWS/ApplicationELB",
            minutes=minutes,
            high_res=False,
        )

    def error_rate_pct(self, service, minutes=5):
        total = self.get_metric("RequestCount", {"Service": service}, "Sum", minutes)
        errors = self.get_metric("RequestErrors", {"Service": service}, "Sum", minutes)
        if not total:
            return 0.0
        return 100.0 * (errors or 0) / total

    def max_clock_offset(self):
        """POS-14. Reads PMT/NFR ClockOffsetSeconds, published by
        scripts/publish_clock_offset.sh; None means that publisher isn't running.
        """
        return self.get_metric("ClockOffsetSeconds", stat="Maximum", minutes=15)

    def up_targets(self):
        """POS-23. Returns {canary_name: 1.0 healthy | 0.0 failing} from
        Synthetics canaries, standing in for Prometheus scrape targets.
        """
        import boto3

        syn = boto3.client("synthetics", region_name=self.region)
        out = {}
        token = None
        while True:
            kw = {"NextToken": token} if token else {}
            page = syn.describe_canaries(**kw)
            for c in page.get("Canaries", []):
                name = c["Name"]
                success = self.get_metric(
                    "SuccessPercent",
                    dimensions={"CanaryName": name},
                    stat="Average",
                    minutes=15,
                    namespace="CloudWatchSynthetics",
                    high_res=False,
                )
                out[name] = 1.0 if (success or 0) >= 100 else 0.0
            token = page.get("NextToken")
            if not token:
                break
        return out


# --------------------------------------------------------------- Prometheus
class Prom:
    """Kept for Amazon Managed Prometheus or a self-hosted deployment."""

    def __init__(self, url=None):
        import requests

        self._requests = requests
        self.url = (url or env("PROMETHEUS_URL", required=True)).rstrip("/")

    def query(self, expr):
        r = self._requests.get(
            self.url + "/api/v1/query", params={"query": expr}, timeout=60
        )
        r.raise_for_status()
        payload = r.json()
        if payload.get("status") != "success":
            raise RuntimeError("prometheus query failed: %s" % expr)
        return payload["data"]["result"]

    def scalar(self, expr, default=None, **kw):
        res = self.query(expr)
        return float(res[0]["value"][1]) if res else default

    def p95_latency(self, service, minutes=5):
        return self.scalar(
            'histogram_quantile(0.95, sum(rate('
            'http_request_duration_seconds_bucket{service="%s"}[%dm])) by (le))'
            % (service, minutes)
        )

    def error_rate_pct(self, service, minutes=5):
        return self.scalar(
            '100 * sum(rate(http_requests_total{service="%s",status=~"5.."}[%dm]))'
            ' / sum(rate(http_requests_total{service="%s"}[%dm]))'
            % (service, minutes, service, minutes),
            default=0.0,
        )

    def max_clock_offset(self):
        return self.scalar("max(abs(node_timex_offset_seconds))", default=None)

    def up_targets(self):
        return {r["metric"].get("job"): float(r["value"][1]) for r in self.query("up")}

    @staticmethod
    def settle(seconds=15):
        time.sleep(seconds)


def get_metrics():
    """Factory. METRICS_BACKEND=cloudwatch (default) | prometheus."""
    backend = env("METRICS_BACKEND", "cloudwatch").lower()
    if backend == "prometheus":
        return Prom()
    if backend == "cloudwatch":
        return CloudWatch()
    raise ValueError("unknown METRICS_BACKEND: %s" % backend)
