"""Deletes everything tagged testbed=ai-registry-crawler in the first region of AWS_REGIONS, then lists what is left.
Run from the repo root: backend/.venv/bin/python testbed/teardown.py"""
from deploy import ROLE_PATH, TAG_KEY, TAG_VALUE

from app.aws import session
from app.crawler.collectors import _pages


def tagged(s):
    lam, logs, iam = s.client("lambda"), s.client("logs"), s.client("iam")
    for fn in _pages(lam, "list_functions", "Functions"):
        if lam.list_tags(Resource=fn["FunctionArn"])["Tags"].get(TAG_KEY) == TAG_VALUE:
            yield "lambda", fn["FunctionName"]
    for group in _pages(logs, "describe_log_groups", "logGroups"):
        if logs.list_tags_log_group(logGroupName=group["logGroupName"])["tags"].get(TAG_KEY) == TAG_VALUE:
            yield "log group", group["logGroupName"]
    for role in _pages(iam, "list_roles", "Roles", PathPrefix=ROLE_PATH):
        if {"Key": TAG_KEY, "Value": TAG_VALUE} in iam.list_role_tags(RoleName=role["RoleName"])["Tags"]:
            yield "iam role", role["RoleName"]


def teardown():
    s = session()
    lam, logs, iam = s.client("lambda"), s.client("logs"), s.client("iam")
    for kind, name in list(tagged(s)):
        if kind == "lambda":
            lam.delete_function(FunctionName=name)
        elif kind == "log group":
            logs.delete_log_group(logGroupName=name)
        else:
            for policy in _pages(iam, "list_role_policies", "PolicyNames", RoleName=name):
                iam.delete_role_policy(RoleName=name, PolicyName=policy)
            iam.delete_role(RoleName=name)
        print(f"deleted {kind} {name}")
    left = list(tagged(s))
    print(f"{len(left)} tagged resources left in {s.region_name}")
    for kind, name in left:
        print(f"  {kind} {name}")


if __name__ == "__main__":
    teardown()
