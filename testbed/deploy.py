"""Deploys a small slice of the seeded estate to the real AWS account, in the first region of AWS_REGIONS.
Nothing here bills while idle: the Lambdas are never invoked and the log groups hold a few spans.
Run from the repo root: backend/.venv/bin/python testbed/deploy.py"""
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.aws import session  # noqa: E402
from app.config import config  # noqa: E402
from app.seed.agent_logs import SONNET, _marketing_steps, _planted, _refund_steps, build_run  # noqa: E402
from app.seed.estate import _lambdas, _zip  # noqa: E402

TAG_KEY, TAG_VALUE = "testbed", "ai-registry-crawler"
ROLE_PATH = "/testbed/"
LAMBDAS = ["refund-agent", "support-chatbot", "ticket-classifier", "image-thumbnailer"]
CRAWLER_ROLE = "ai-registry-crawler-readonly"


def _role(iam, name, principal, policy):
    trust = json.dumps({"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": principal, "Action": "sts:AssumeRole"},
    ]})
    try:
        iam.create_role(RoleName=name, Path=ROLE_PATH, AssumeRolePolicyDocument=trust,
                        Tags=[{"Key": TAG_KEY, "Value": TAG_VALUE}])
    except iam.exceptions.EntityAlreadyExistsException:
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=trust)
        iam.tag_role(RoleName=name, Tags=[{"Key": TAG_KEY, "Value": TAG_VALUE}])
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-policy", PolicyDocument=json.dumps(policy))
    return iam.get_role(RoleName=name)["Role"]["Arn"]


def _when_role_ready(call, attempts=10):
    # a new IAM role takes a few seconds to become assumable by Lambda
    for attempt in range(attempts):
        try:
            return call()
        except Exception as e:
            if "cannot be assumed" not in str(e) or attempt == attempts - 1:
                raise
            time.sleep(3)


def _function(lam, fn, role_arn):
    name, code, tags = fn["name"], _zip(fn["src"]), fn["tags"] | {TAG_KEY: TAG_VALUE}
    settings = {"Role": role_arn, "Runtime": "python3.12", "Handler": "handler.lambda_handler",
                "Environment": {"Variables": fn["env"]}, "Timeout": 60, "MemorySize": 512}
    try:
        _when_role_ready(lambda: lam.create_function(FunctionName=name, Code={"ZipFile": code}, Tags=tags, **settings))
        return "created"
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(FunctionName=name, ZipFile=code)
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        arn = _when_role_ready(lambda: lam.update_function_configuration(FunctionName=name, **settings))["FunctionArn"]
        lam.tag_resource(Resource=arn, Tags=tags)
        return "updated"


def build_spans(now):
    """A few normal refund runs, the $1,200 refund with no approval (case a) and the unregistered marketing agent."""
    rng = random.Random(42)

    def start():
        # one put_log_events batch may not span more than 24 hours
        return now - timedelta(minutes=rng.randint(30, 20 * 60))

    refund_no_approval = _planted()[0]
    refund = [s for _ in range(5) for s in build_run(rng, "refund-agent", SONNET, start(), _refund_steps(rng))]
    refund += build_run(rng, *refund_no_approval[:2], start(), *refund_no_approval[2:])
    marketing = [s for _ in range(3) for s in build_run(rng, "marketing-copy-agent", "gpt-4o", start(), _marketing_steps(rng))]
    return {"refund-agent": refund, "marketing-copy-agent": marketing}


def _log_group(logs, agent, spans):
    group = f"{config.agent_log_prefix}{agent}"
    try:
        logs.create_log_group(logGroupName=group, tags={TAG_KEY: TAG_VALUE})
    except logs.exceptions.ResourceAlreadyExistsException:
        logs.tag_log_group(logGroupName=group, tags={TAG_KEY: TAG_VALUE})
    try:
        logs.create_log_stream(logGroupName=group, logStreamName="testbed")
    except logs.exceptions.ResourceAlreadyExistsException:
        # events can't be deleted, and a deleted-then-recreated group may show its old events again
        return group, 0
    events = [{"timestamp": int(datetime.fromisoformat(s["start_time"]).timestamp() * 1000), "message": json.dumps(s)}
              for s in spans]
    logs.put_log_events(logGroupName=group, logStreamName="testbed",
                        logEvents=sorted(events, key=lambda e: e["timestamp"]))
    return group, len(events)


def deploy(now=None):
    s = session()
    account = s.client("sts").get_caller_identity()["Account"]
    iam, lam, logs = s.client("iam"), s.client("lambda"), s.client("logs")
    print(f"deploying to {s.region_name}")

    for fn in [f for f in _lambdas(s.region_name, account) if f["name"] in LAMBDAS]:
        policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": fn["actions"], "Resource": "*"}]}
        role_arn = _role(iam, f"testbed-{fn['name']}", {"Service": "lambda.amazonaws.com"}, policy)
        print(f"lambda {fn['name']}: {_function(lam, fn, role_arn)}")

    for agent, spans in build_spans(now or datetime.now(timezone.utc)).items():
        group, written = _log_group(logs, agent, spans)
        print(f"log group {group}: {written} spans written" + ("" if written else " (already there)"))

    policy = json.loads((ROOT / "docs" / "crawler-iam-policy.json").read_text())
    print(f"crawler role {_role(iam, CRAWLER_ROLE, {'AWS': f'arn:aws:iam::{account}:root'}, policy)}")


if __name__ == "__main__":
    deploy()
