"""Read-only Prometheus acceptance: exact names, redis_up=1 and fresh samples."""
import argparse
import json
import time
import urllib.parse
import urllib.request


def verify(result, names, max_age=120):
    if result.get("status") != "success":
        raise ValueError("Prometheus query failed")
    series = result["data"]["result"]
    for name in names:
        matches = [s for s in series if s["metric"].get("redis_name") == name]
        if not matches or any(float(s["value"][1]) != 1 for s in matches):
            raise ValueError(f"No healthy recent redis_up for {name}")
        if any(time.time() - float(s["value"][0]) > max_age for s in matches):
            raise ValueError(f"Stale Redis sample: {name}")
        for sample in matches:
            for label in ("aws_account_id", "aws_region", "aws_resource_type", "environment", "project"):
                if not sample["metric"].get(label):
                    raise ValueError(f"Missing {label}: {name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:19090")
    parser.add_argument("--names", nargs="+", required=True)
    args = parser.parse_args()
    # timestamp() returns the source sample time; an instant query's own
    # evaluation timestamp alone would not prove sample freshness.
    params = urllib.parse.urlencode({"query": "redis_up and (time() - timestamp(redis_up) < 120)"})
    with urllib.request.urlopen(args.url.rstrip("/") + "/api/v1/query?" + params, timeout=15) as response:
        result = json.load(response)
    verify(result, args.names)
    print("PASS: Redis healthy, source samples <120s, required labels present")
