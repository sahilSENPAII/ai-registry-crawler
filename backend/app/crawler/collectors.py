"""Read-only collectors: one per AWS service, each returning raw facts as Resources.
Only List*/Get*/Describe* calls; nothing here decides what is AI."""
import json
from dataclasses import dataclass, field
from functools import lru_cache, partial, update_wrapper

import boto3

from app.config import config
from app.crawler.masking import mask, mask_text


@dataclass
class Resource:
    service: str
    name: str
    arn: str
    account_id: str
    region: str
    tags: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    code: dict = field(default_factory=dict)
    role_arn: str | None = None
    iam_actions: list = field(default_factory=list)
    log_groups: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _resource(session, service, name, arn, role_arn=None, **facts) -> Resource:
    parts = arn.split(":")
    r = Resource(service, name, arn, account_id=parts[4], region=parts[3], role_arn=role_arn, **facts)
    r.tags, r.env, r.config = mask(r.tags), mask(r.env), mask(r.config)
    if role_arn:
        try:
            r.iam_actions = list(_role_actions(session, role_arn))
        except Exception as e:
            r.errors.append(f"iam: {e}")
    return r


def _pages(client, method, key, **kwargs):
    for page in client.get_paginator(method).paginate(**kwargs):
        yield from page[key]


@lru_cache(maxsize=None)
def _role_actions(session: boto3.Session, role_arn: str) -> tuple[str, ...]:
    # shortcut: Allow statements only, ignores Deny/NotAction/conditions; upgrade if unused-permission findings need exact effective access
    iam = session.client("iam")
    role = role_arn.split("/")[-1]
    documents = [iam.get_role_policy(RoleName=role, PolicyName=p)["PolicyDocument"]
                 for p in _pages(iam, "list_role_policies", "PolicyNames", RoleName=role)]
    for attached in _pages(iam, "list_attached_role_policies", "AttachedPolicies", RoleName=role):
        version = iam.get_policy(PolicyArn=attached["PolicyArn"])["Policy"]["DefaultVersionId"]
        documents.append(iam.get_policy_version(PolicyArn=attached["PolicyArn"], VersionId=version)["PolicyVersion"]["Document"])
    actions = set()
    for doc in documents:
        statements = doc["Statement"] if isinstance(doc["Statement"], list) else [doc["Statement"]]
        for st in statements:
            if st.get("Effect") == "Allow" and "Action" in st:
                actions.update([st["Action"]] if isinstance(st["Action"], str) else st["Action"])
    return tuple(sorted(actions))


def _attach_log_groups(session, resources):
    prefix = config.agent_log_prefix
    existing = {g["logGroupName"] for g in _pages(session.client("logs"), "describe_log_groups", "logGroups",
                                                  logGroupNamePrefix=prefix)}
    for r in resources:
        named = [g for g in (r.tags.get("ai:log-group"), f"{prefix}{r.name}" if f"{prefix}{r.name}" in existing else None) if g]
        r.log_groups = sorted(set(r.log_groups + named))
    return resources


def collect_lambda(session, fetch_code):
    lam = session.client("lambda")
    resources = []
    for fn in _pages(lam, "list_functions", "Functions"):
        r = _resource(
            session, "lambda", fn["FunctionName"], fn["FunctionArn"], fn["Role"],
            tags=lam.list_tags(Resource=fn["FunctionArn"])["Tags"],
            env=fn.get("Environment", {}).get("Variables", {}),
            config={"runtime": fn.get("Runtime"), "handler": fn.get("Handler"), "description": fn.get("Description", "")},
        )
        try:
            r.code = {path: mask_text(text) for path, text in fetch_code(fn["FunctionName"]).items()}
        except Exception as e:
            r.errors.append(f"code: {e}")
        resources.append(r)
    return _attach_log_groups(session, resources)


def collect_bedrock_agents(session):
    client = session.client("bedrock-agent")
    resources = []
    for summary in _pages(client, "list_agents", "agentSummaries"):
        agent = client.get_agent(agentId=summary["agentId"])["agent"]
        resources.append(_resource(
            session, "bedrock-agent", agent["agentName"], agent["agentArn"], agent.get("agentResourceRoleArn"),
            tags=client.list_tags_for_resource(resourceArn=agent["agentArn"])["tags"],
            config={"foundation_model": agent.get("foundationModel"), "instruction": agent.get("instruction"),
                    "description": agent.get("description"), "status": agent.get("agentStatus")},
        ))
    return _attach_log_groups(session, resources)


def collect_sagemaker(session):
    sm = session.client("sagemaker")
    resources = []
    for ep in _pages(sm, "list_endpoints", "Endpoints"):
        endpoint = sm.describe_endpoint(EndpointName=ep["EndpointName"])
        endpoint_config = sm.describe_endpoint_config(EndpointConfigName=endpoint["EndpointConfigName"])
        models, env, role_arn = [], {}, None
        for variant in endpoint_config["ProductionVariants"]:
            model = sm.describe_model(ModelName=variant["ModelName"])
            containers = model.get("Containers") or [model["PrimaryContainer"]]
            models += [{"name": model["ModelName"], "image": c.get("Image"), "model_data_url": c.get("ModelDataUrl"),
                        "instance_type": variant.get("InstanceType")} for c in containers]
            for c in containers:
                env |= c.get("Environment", {})
            role_arn = model.get("ExecutionRoleArn")
        tags = sm.list_tags(ResourceArn=ep["EndpointArn"])["Tags"]
        resources.append(_resource(
            session, "sagemaker", ep["EndpointName"], ep["EndpointArn"], role_arn,
            tags={t["Key"]: t["Value"] for t in tags}, env=env,
            config={"endpoint_config": endpoint["EndpointConfigName"], "models": models},
        ))
    return _attach_log_groups(session, resources)


def collect_stepfunctions(session):
    sfn = session.client("stepfunctions")
    resources = []
    for summary in _pages(sfn, "list_state_machines", "stateMachines"):
        machine = sfn.describe_state_machine(stateMachineArn=summary["stateMachineArn"])
        tags = sfn.list_tags_for_resource(resourceArn=summary["stateMachineArn"])["tags"]
        resources.append(_resource(
            session, "stepfunctions", machine["name"], machine["stateMachineArn"], machine["roleArn"],
            tags={t["key"]: t["value"] for t in tags},
            config={"type": machine.get("type"), "definition": json.loads(machine["definition"])},
        ))
    return _attach_log_groups(session, resources)


def collect_ecs(session):
    ecs = session.client("ecs")
    resources = []
    for family in _pages(ecs, "list_task_definition_families", "families", status="ACTIVE"):
        described = ecs.describe_task_definition(taskDefinition=family, include=["TAGS"])  # family alone = latest revision
        td = described["taskDefinition"]
        containers, env, log_groups = [], {}, []
        for c in td["containerDefinitions"]:
            containers.append({"name": c["name"], "image": c["image"], "log_configuration": c.get("logConfiguration")})
            env |= {e["name"]: e["value"] for e in c.get("environment", [])}
            group = (c.get("logConfiguration") or {}).get("options", {}).get("awslogs-group")
            if group:
                log_groups.append(group)
        resources.append(_resource(
            session, "ecs", td["family"], td["taskDefinitionArn"], td.get("taskRoleArn"),
            tags={t["key"]: t["value"] for t in described.get("tags", [])}, env=env,
            config={"revision": td["revision"], "containers": containers}, log_groups=log_groups,
        ))
    return _attach_log_groups(session, resources)


def collectors(fetch_code):
    lam = update_wrapper(partial(collect_lambda, fetch_code=fetch_code), collect_lambda)
    return [lam, collect_bedrock_agents, collect_sagemaker, collect_stepfunctions, collect_ecs]


def collect_all(session, fetch_code) -> tuple[list[Resource], list[str]]:
    resources, errors = [], []
    for collect in collectors(fetch_code):
        try:
            resources += collect(session)
        except Exception as e:
            errors.append(f"{collect.__name__}: {e}")
    return resources, errors
