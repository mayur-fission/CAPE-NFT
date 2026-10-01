"""Load-and-performance preflight. Run with: make check

Confirms the harness is wired up and reports which thresholds still block a
real verdict. It tests nothing about the system under test.
"""
import sys

from common import config


def main():
    ok = True
    print("PMT load and performance preflight\n" + "=" * 52)

    print("\n[1] environment")
    for var in ("PMT_BASE_URL", "PMT_USER", "PMT_PASSWORD", "PMT_TEST_PROJECT_ID"):
        val = config.env(var)
        print("    %-24s %s" % (var, "set" if val else "MISSING"))
        if not val:
            ok = False

    print("\n[2] PMT connectivity")
    try:
        from common.client import PMTClient

        c = PMTClient()
        code, _ = c.health_details()
        print("    /health/details -> HTTP %s" % code)
    except Exception as exc:
        print("    FAILED: %s" % exc)
        ok = False

    print("\n[3] metrics backend (optional for load runs)")
    backend = config.env("METRICS_BACKEND", "cloudwatch")
    try:
        from common.metrics import get_metrics

        get_metrics()
        print("    %s reachable" % backend)
    except Exception as exc:
        print("    %s unavailable: %s" % (backend, exc))
        print("    Locust runs will still work; resource-side analysis will not.")

    print("\n[4] baseline")
    import os

    base = os.path.join("evidence", "locust", "baseline_stats.csv")
    if os.path.exists(base):
        print("    baseline present: %s" % base)
    else:
        print("    NO BASELINE. Run 'make baseline' first.")
        print("    Every degradation threshold is relative to it, so load")
        print("    results before it exists cannot be interpreted.")

    print("\n[5] phase-00 threshold gate")
    unset = config.unset_thresholds()
    if not unset:
        print("    all thresholds agreed")
    else:
        print("    %d still unset - affected tests will SKIP:" % len(unset))
        for key, oq in unset:
            print("      %-36s %s" % (key, oq))

    print("\n" + "=" * 52)
    if not ok:
        print("PREFLIGHT FAILED - fix the above before running anything")
        return 1
    if unset:
        print("Preflight OK, thresholds incomplete. Load runs will execute;")
        print("their results are not acceptance evidence until phase 00 closes.")
    else:
        print("Ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
