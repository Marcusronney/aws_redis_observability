#!/usr/bin/env python3
"""Signed PromQL query against Amazon Managed Service for Prometheus (not the Kind server)."""
import argparse
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", required=True, help="terraform central query_endpoint")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--profile", default="redis-observability-admin")
    ap.add_argument("--promql", default='redis_up{job="redis-aws-distributed"}')
    args = ap.parse_args()
    url = args.endpoint.rstrip("/") + "/api/v1/query?" + urlencode({"query": args.promql})
    session = boto3.Session(profile_name=args.profile)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials unavailable")
    awsreq = AWSRequest(method="GET", url=url)
    SigV4Auth(credentials.get_frozen_credentials(), "aps", args.region).add_auth(awsreq)
    with urlopen(Request(url, headers=dict(awsreq.headers.items())), timeout=20) as response:
        data = json.load(response)
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
