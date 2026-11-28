"""Re-runs discovery as the read-only crawler role from deploy.py, to check docs/crawler-iam-policy.json is enough.
Run from the repo root: DISCOVERY_MODE=aws backend/.venv/bin/python testbed/crawl_readonly.py"""
import os
import sys

from deploy import CRAWLER_ROLE, ROLE_PATH

from app import aws, cli
from app.config import config
from app.crawler.collectors import collect_all


def main():
    if config.discovery_mode != "aws":
        sys.exit("set DISCOVERY_MODE=aws")
    sts = aws.session().client("sts")
    role_arn = f"arn:aws:iam::{sts.get_caller_identity()['Account']}:role{ROLE_PATH}{CRAWLER_ROLE}"
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="ai-registry-crawler")["Credentials"]
    # this process only; never printed or written anywhere
    os.environ.update(AWS_ACCESS_KEY_ID=creds["AccessKeyId"], AWS_SECRET_ACCESS_KEY=creds["SecretAccessKey"],
                      AWS_SESSION_TOKEN=creds["SessionToken"])
    print(f"crawling as {aws.session().client('sts').get_caller_identity()['Arn']}")

    # per-resource errors (code download, IAM reads) don't fail a run, so list them here
    resources, _ = collect_all(aws.session(), aws.lambda_code_fetcher("aws"))
    for r in resources:
        for error in r.errors:
            print(f"resource error: {r.name}: {error}")
    cli.discover()


if __name__ == "__main__":
    main()
