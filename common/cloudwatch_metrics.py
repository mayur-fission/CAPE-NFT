"""Best-effort CloudWatch snapshots of the Workbench host (AWS/EC2) and its
database (AWS/RDS), captured alongside a scenario for correlation only. Never
raises: these metrics are supplementary, not a reason to fail a test.

Configured by RSTUDIO_SERVER_INSTANCE_ID, RSTUDIO_DB_INSTANCE_ID and
AWS_REGION (default eu-west-1); a snapshot is skipped when its id is unset.
"""
import datetime

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


def _latest_value(cloudwatch, namespace, metric_name, dimensions, minutes):
    """Most recent 1-minute average of one metric over the last `minutes`,
    or None when there is no data."""
    end = datetime.datetime.now(datetime.timezone.utc)
    resp = cloudwatch.get_metric_data(
        MetricDataQueries=[{
            "Id": "q1",
            "MetricStat": {
                "Metric": {
                    "Namespace": namespace,
                    "MetricName": metric_name,
                    "Dimensions": [{"Name": k, "Value": v} for k, v in dimensions.items()],
                },
                "Period": 60,
                "Stat": "Average",
            },
            "ReturnData": True,
        }],
        StartTime=end - datetime.timedelta(minutes=minutes),
        EndTime=end,
        ScanBy="TimestampDescending",
    )
    values = resp["MetricDataResults"][0]["Values"]
    return float(values[0]) if values else None


def _capture(kind, id_env_name, resource_id, namespace, dimension, metric_names, minutes):
    """{metric_name: value or None} for one resource, or {} when it is not
    configured or CloudWatch can't be reached."""
    resource_id = resource_id or env(id_env_name)
    if not resource_id:
        print("[rstudio-local] %s not set - skipping %s metrics" % (id_env_name, kind))
        return {}
    try:
        import boto3

        cloudwatch = boto3.client("cloudwatch", region_name=env("AWS_REGION", "eu-west-1"))
        values = {
            name: _latest_value(cloudwatch, namespace, name, {dimension: resource_id}, minutes)
            for name in metric_names
        }
    except Exception as exc:
        print("[rstudio-local] CloudWatch unavailable - skipping %s metrics: %s" % (kind, exc))
        return {}
    print("[rstudio-local] %s metrics for %s (last %dm): %s" % (kind, resource_id, minutes, values))
    return values


def capture_server_metrics(minutes=5, instance_id=None):
    """Snapshot the Workbench host's EC2 metrics (instance_id default
    RSTUDIO_SERVER_INSTANCE_ID) over the last `minutes`."""
    return _capture("server", "RSTUDIO_SERVER_INSTANCE_ID", instance_id,
                    "AWS/EC2", "InstanceId", SERVER_METRIC_NAMES, minutes)


def capture_db_metrics(minutes=5, db_instance_id=None):
    """Snapshot the database's RDS metrics (db_instance_id default
    RSTUDIO_DB_INSTANCE_ID) over the last `minutes`."""
    return _capture("DB", "RSTUDIO_DB_INSTANCE_ID", db_instance_id,
                    "AWS/RDS", "DBInstanceIdentifier", DB_METRIC_NAMES, minutes)
