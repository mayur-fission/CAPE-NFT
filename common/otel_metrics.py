"""OpenTelemetry metrics for session-launch scenarios, optionally exposed
for Prometheus (see common/observability.py for the rest of the stack).
"""
from common.config import env

try:
    from opentelemetry import metrics as _otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider as _OtelMeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter as _OtelConsoleMetricExporter
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader as _OtelPeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource as _OtelResource
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

_otel_meter_provider = None
_otel_prometheus_port = None


def enable_prometheus_metrics_export(port=9464):
    """Expose OTel metrics on http://localhost:<port>/metrics for Prometheus.

    Must be called before the first capture_otel_metrics(), since the
    MeterProvider's readers are fixed when it's built; raises RuntimeError
    otherwise. Returns False if the Prometheus exporter isn't installed.
    """
    global _otel_prometheus_port
    try:
        import opentelemetry.exporter.prometheus  # noqa: F401
    except ImportError:
        print("[rstudio-local] opentelemetry-exporter-prometheus not installed - skipping")
        return False
    if _otel_meter_provider is not None:
        raise RuntimeError(
            "enable_prometheus_metrics_export() must be called before the first "
            "capture_otel_metrics() - the meter provider is already built"
        )
    _otel_prometheus_port = port
    return True


def _get_otel_meter():
    """Return the process-wide OTel meter, building its MeterProvider on first
    use. Exports to the console, or to OTEL_EXPORTER_OTLP_ENDPOINT if set.
    """
    global _otel_meter_provider
    if _otel_meter_provider is None:
        resource = _OtelResource.create({
            "service.name": env("RSTUDIO_OTEL_SERVICE_NAME", "rstudio-session-perf"),
        })
        readers = []

        endpoint = env("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            exporter = OTLPMetricExporter(endpoint=endpoint)
        else:
            exporter = _OtelConsoleMetricExporter()
        readers.append(_OtelPeriodicExportingMetricReader(exporter, export_interval_millis=60000))

        if _otel_prometheus_port:
            from prometheus_client import start_http_server
            from opentelemetry.exporter.prometheus import PrometheusMetricReader
            start_http_server(port=_otel_prometheus_port)
            readers.append(PrometheusMetricReader())
            print(
                "[rstudio-local] OTel metrics exposed for Prometheus scraping at "
                "http://localhost:%d/metrics" % _otel_prometheus_port
            )

        _otel_meter_provider = _OtelMeterProvider(resource=resource, metric_readers=readers)
        _otel_metrics.set_meter_provider(_otel_meter_provider)
    return _otel_metrics.get_meter("rstudio.session.perf")


def capture_otel_metrics(results, run_elapsed, scenario="launch_performance"):
    """Record launch duration, bytes received, attempt count (by status) and
    run throughput for a scenario's results, tagged with `scenario`.

    Returns False if OpenTelemetry isn't installed, True once flushed.
    """
    if not _HAS_OTEL:
        print("[rstudio-local] opentelemetry not installed - skipping OTel metrics capture")
        return False

    meter = _get_otel_meter()
    duration_histogram = meter.create_histogram(
        "rstudio.session.launch.duration", unit="s",
        description="Time from clicking Launch to the IDE being visible",
    )
    bytes_histogram = meter.create_histogram(
        "rstudio.session.launch.bytes_received", unit="By",
        description="Response bytes received while a session was launching",
    )
    launch_counter = meter.create_counter(
        "rstudio.session.launch.count", description="Session launch attempts",
    )
    throughput_histogram = meter.create_histogram(
        "rstudio.session.launch.throughput", unit="1/s",
        description="Sessions launched per second, this run",
    )

    ok_results = [r for r in results if r["ok"]]
    for r in results:
        attrs = {"scenario": scenario, "session_index": r["index"]}
        if r["ok"]:
            duration_histogram.record(r["elapsed_s"], attrs)
            bytes_histogram.record(r["bytes_received"], attrs)
            launch_counter.add(1, dict(attrs, status="ok"))
        else:
            launch_counter.add(1, dict(attrs, status="failed"))

    if ok_results and run_elapsed:
        throughput_histogram.record(len(ok_results) / run_elapsed, {"scenario": scenario})

    _otel_meter_provider.force_flush()
    print(
        "[rstudio-local] captured OTel metrics for %d session(s) (scenario=%s, %d ok, %d failed)"
        % (len(results), scenario, len(ok_results), len(results) - len(ok_results))
    )
    return True
