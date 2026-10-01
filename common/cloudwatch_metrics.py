"""Best-effort CloudWatch snapshots of the Workbench host (AWS/EC2) and its
database (AWS/RDS), captured alongside a scenario for correlation only.
"""
from common.config import env

# Standard AWS/EC2 and AWS/RDS metrics (5-minute resolution, no agent needed).
SERVER_METRIC_NAMES = (
    "CPUUtilization", "DiskReadBytes", "DiskWriteBytes",
    "DiskReadOps", "DiskWriteOps", "NetworkIn", "NetworkOut",
)
DB_METRIC_NAMES = (
    "CPUUtilization", "DatabaseConnections", "ReadIOPS", "WriteIOPS",
    "ReadLatency", "WriteLatency", "FreeableMemory", "DiskQueueDepth",
)


def _cloudwatch_backend_or_none(purpose):
    """Return the metrics backend if it's CloudWatch, else None (printing why).
    Never raises - these metrics are supplementary, not a reason to fail.
    """
    try:
        from common.metrics import CloudWatch, get_metrics

        backend = get_metrics()
    except Exception as exc:
        print("[rstudio-local] metrics backend unavailable - skipping %s metrics: %s" % (purpose, exc))
        return None
    if not isinstance(backend, CloudWatch):
        print(
            "[rstudio-local] %s metrics need METRICS_BACKEND=cloudwatch "
            "(the AWS/EC2 and AWS/RDS namespaces used here are CloudWatch-"
            "specific) - skipping" % purpose
        )
        return None
    return backend


def capture_server_metrics(minutes=5, instance_id=None):
    """Snapshot the Workbench host's EC2 metrics over the last `minutes`.

    instance_id defaults to RSTUDIO_SERVER_INSTANCE_ID. Returns
    {metric_name: value or None}, or {} if unconfigured.
    """
    instance_id = instance_id or env("RSTUDIO_SERVER_INSTANCE_ID")
    if not instance_id:
        print("[rstudio-local] RSTUDIO_SERVER_INSTANCE_ID not set - skipping server metrics")
        return {}

    backend = _cloudwatch_backend_or_none("server")
    if backend is None:
        return {}

    values = {
        name: backend.get_metric(
            name, dimensions={"InstanceId": instance_id}, namespace="AWS/EC2",
            minutes=minutes, high_res=False,
        )
        for name in SERVER_METRIC_NAMES
    }
    print("[rstudio-local] server metrics for EC2 %s (last %dm): %s" % (instance_id, minutes, values))
    return values


def capture_db_metrics(minutes=5, db_instance_id=None):
    """Same as capture_server_metrics(), for the RDS instance
    (RSTUDIO_DB_INSTANCE_ID).
    """
    db_instance_id = db_instance_id or env("RSTUDIO_DB_INSTANCE_ID")
    if not db_instance_id:
        print("[rstudio-local] RSTUDIO_DB_INSTANCE_ID not set - skipping DB metrics")
        return {}

    backend = _cloudwatch_backend_or_none("DB")
    if backend is None:
        return {}

    values = {
        name: backend.get_metric(
            name, dimensions={"DBInstanceIdentifier": db_instance_id}, namespace="AWS/RDS",
            minutes=minutes, high_res=False,
        )
        for name in DB_METRIC_NAMES
    }
    print("[rstudio-local] DB metrics for RDS %s (last %dm): %s" % (db_instance_id, minutes, values))
    return values
