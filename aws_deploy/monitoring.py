# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
# ]
# ///

"""CloudWatch checks and a load-test dashboard for the deployed e-Mailbox (v2).

Usage (Learner Lab credentials exported or in .env):
    uv run aws_deploy/monitoring.py check       # read-only: can we see our metrics/logs?
    uv run aws_deploy/monitoring.py dashboard   # create/update the CloudWatch dashboard

The dashboard (`cs361-g3-loadtest`) shows, for the API and each Lambda:
traffic, latency (p50/p95/p99), errors, throttles, concurrency and cold starts,
i.e. what you need to spot the bottleneck while Locust runs.

MongoDB Atlas is not an AWS service: watch it in Atlas -> Cluster -> Metrics.
AWS credits are only shown in the Learner Lab console (top of the lab page).
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import get_aws_session  # noqa: E402

# Keep in sync with MICRO_FUNCTIONS / api_name in deploy_v2.py.
FUNCTIONS = ["cs361-g3-v2-documents", "cs361-g3-v2-search", "cs361-g3-v2-auth"]
API_NAME = "cs361-g3-v2-api"
DASHBOARD_NAME = "cs361-g3-loadtest"


def find_api_id(apigw) -> str | None:
    for api in apigw.get_apis().get("Items", []):
        if api.get("Name") == API_NAME:
            return api["ApiId"]
    return None


def metric_sum(cw, namespace, name, dims, hours=24) -> float | None:
    """Sum of a metric over the last `hours`; None if CloudWatch refused the call."""
    end = datetime.now(timezone.utc)
    resp = cw.get_metric_statistics(
        Namespace=namespace, MetricName=name, Dimensions=dims,
        StartTime=end - timedelta(hours=hours), EndTime=end,
        Period=3600, Statistics=["Sum"],
    )
    return sum(p["Sum"] for p in resp.get("Datapoints", []))


# ---------------------------------------------------------------------------- check


def check(args):
    session, account_id, region = get_aws_session()
    cw = session.client("cloudwatch")
    logs = session.client("logs")
    lam = session.client("lambda")
    apigw = session.client("apigatewayv2")
    ok = True

    def line(status, msg):
        print(f"  [{status}] {msg}")

    print("\n== CloudWatch access")
    try:
        cw.list_dashboards()
        line("OK", "cloudwatch:ListDashboards allowed")
    except ClientError as err:
        ok = False
        line("FAIL", f"CloudWatch not accessible: {err.response['Error']['Code']}")

    print("\n== Lambda functions (last 24h)")
    for name in FUNCTIONS:
        try:
            cfg = lam.get_function_configuration(FunctionName=name)
        except ClientError:
            ok = False
            line("MISSING", f"{name} not deployed (run deploy_v2.py)")
            continue
        dims = [{"Name": "FunctionName", "Value": name}]
        inv = metric_sum(cw, "AWS/Lambda", "Invocations", dims)
        err = metric_sum(cw, "AWS/Lambda", "Errors", dims)
        thr = metric_sum(cw, "AWS/Lambda", "Throttles", dims)
        line("OK", f"{name}: {cfg['MemorySize']} MB, timeout {cfg['Timeout']}s | "
                   f"invocations {inv:.0f}, errors {err:.0f}, throttles {thr:.0f}")

        group = f"/aws/lambda/{name}"
        try:
            streams = logs.describe_log_streams(
                logGroupName=group, orderBy="LastEventTime", descending=True, limit=1
            ).get("logStreams", [])
            if streams and streams[0].get("lastEventTimestamp"):
                last = datetime.fromtimestamp(streams[0]["lastEventTimestamp"] / 1000, timezone.utc)
                line("OK", f"  logs: {group} (last event {last:%Y-%m-%d %H:%M} UTC)")
            else:
                line("WARN", f"  logs: {group} exists but is empty, call the API once")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                line("WARN", f"  logs: {group} not created yet, call the API once")
            else:
                ok = False
                line("FAIL", f"  logs: {e.response['Error']['Code']}")

    print("\n== API Gateway")
    api_id = find_api_id(apigw)
    if not api_id:
        ok = False
        line("MISSING", f"{API_NAME} not found")
    else:
        count = metric_sum(cw, "AWS/ApiGateway", "Count", [{"Name": "ApiId", "Value": api_id}])
        line("OK", f"{API_NAME} ({api_id}): {count:.0f} requests in last 24h")
        line("OK", f"  URL: https://{api_id}.execute-api.{region}.amazonaws.com")

    print("\n== Scaling limits (these matter for the load test)")
    try:
        limits = lam.get_account_settings()["AccountLimit"]
        conc = limits.get("ConcurrentExecutions")
        unreserved = limits.get("UnreservedConcurrentExecutions")
        line("INFO", f"Lambda account concurrency limit: {conc} (unreserved {unreserved})")
        if conc is not None and conc <= 10:
            line("INFO", "  Learner Lab caps Lambda at ~10 concurrent runs: expect Throttles "
                         "(HTTP 503/429) once Locust pushes past that")
    except ClientError as e:
        line("WARN", f"could not read Lambda limits: {e.response['Error']['Code']}")

    print("\n== Not in CloudWatch")
    line("INFO", "MongoDB Atlas metrics: Atlas UI -> your cluster -> Metrics")
    line("INFO", "AWS credits left: Learner Lab page (top bar), no API for it")

    try:
        dashboards = [d["DashboardName"] for d in cw.list_dashboards().get("DashboardEntries", [])]
        if DASHBOARD_NAME in dashboards:
            line("OK", f"dashboard exists: {dashboard_url(region)}")
        else:
            line("INFO", "no dashboard yet: run `monitoring.py dashboard`")
    except ClientError:
        pass

    print("\nRESULT:", "CloudWatch is usable" if ok else "something is missing (see above)")
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------------ dashboard


def dashboard_url(region: str) -> str:
    return (f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}"
            f"#dashboards/dashboard/{DASHBOARD_NAME}")


def build_dashboard(region: str, api_id: str | None) -> dict:
    widgets = []
    y = 0

    def add(title, metrics, x, w=8, h=6, stat="Sum", period=60, yaxis=None, view="timeSeries"):
        props = {"title": title, "region": region, "metrics": metrics, "stat": stat,
                 "period": period, "view": view, "stacked": False}
        if yaxis:
            props["yAxis"] = {"left": yaxis}
        widgets.append({"type": "metric", "x": x, "y": y, "width": w, "height": h, "properties": props})

    widgets.append({"type": "text", "x": 0, "y": y, "width": 24, "height": 2, "properties": {"markdown": (
        "## CS361 G3 load test\n"
        "1-minute resolution. Set the time range to **15m** and auto-refresh to **10s** while Locust runs. "
        "Bottleneck hints: rising p95 with flat errors = slow code/DB; Throttles > 0 = Lambda concurrency cap; "
        "5xx with Duration near timeout = DB connection or timeout."
    )}})
    y += 2

    if api_id:
        a = ["AWS/ApiGateway"]
        add("API requests / min", [a + ["Count", "ApiId", api_id]], 0)
        add("API latency (ms)", [
            a + ["Latency", "ApiId", api_id, {"stat": "p50", "label": "p50"}],
            a + ["Latency", "ApiId", api_id, {"stat": "p95", "label": "p95"}],
            a + ["Latency", "ApiId", api_id, {"stat": "p99", "label": "p99"}],
            a + ["IntegrationLatency", "ApiId", api_id, {"stat": "p95", "label": "Lambda side p95"}],
        ], 8)
        add("API errors / min", [
            a + ["4xx", "ApiId", api_id, {"label": "4xx"}],
            a + ["5xx", "ApiId", api_id, {"label": "5xx", "color": "#d62728"}],
        ], 16)
        y += 6

    def per_fn(metric, **opts):
        return [["AWS/Lambda", metric, "FunctionName", fn, {"label": fn.replace("cs361-g3-v2-", ""), **opts}]
                for fn in FUNCTIONS]

    add("Lambda invocations / min", per_fn("Invocations"), 0)
    add("Lambda duration p95 (ms)", per_fn("Duration", stat="p95"), 8)
    add("Lambda duration max (ms)", per_fn("Duration", stat="Maximum"), 16)
    y += 6
    add("Lambda concurrent executions", per_fn("ConcurrentExecutions", stat="Maximum")
        + [["AWS/Lambda", "ConcurrentExecutions", {"stat": "Maximum", "label": "account total"}]], 0)
    add("Lambda throttles / min", per_fn("Throttles"), 8)
    add("Lambda errors / min", per_fn("Errors"), 16)
    y += 6

    sources = " | ".join(f"SOURCE '/aws/lambda/{fn}'" for fn in FUNCTIONS)
    widgets.append({"type": "log", "x": 0, "y": y, "width": 24, "height": 6, "properties": {
        "title": "Cold starts and memory per minute (from Lambda REPORT logs)",
        "region": region, "view": "table",
        "query": (f"{sources} | filter @type = \"REPORT\" "
                  "| stats count(*) as invocations, count(@initDuration) as cold_starts, "
                  "avg(@initDuration) as avg_init_ms, pct(@duration, 95) as p95_ms, "
                  "max(@maxMemoryUsed) / 1000 / 1000 as max_mem_mb by bin(1m) "
                  "| sort bin(1m) desc"),
    }})
    return {"widgets": widgets}


def dashboard(args):
    session, _, region = get_aws_session()
    cw = session.client("cloudwatch")
    api_id = find_api_id(session.client("apigatewayv2"))
    if not api_id:
        print(f"warn: {API_NAME} not found, dashboard will only show Lambda widgets")
    body = build_dashboard(region, api_id)
    cw.put_dashboard(DashboardName=DASHBOARD_NAME, DashboardBody=json.dumps(body))
    print(f"\ndone: dashboard '{DASHBOARD_NAME}' created/updated")
    print(f"      {dashboard_url(region)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="read-only check that CloudWatch shows our resources")
    sub.add_parser("dashboard", help="create/update the load-test CloudWatch dashboard")
    args = parser.parse_args()
    {"check": check, "dashboard": dashboard}[args.command](args)


if __name__ == "__main__":
    main()
