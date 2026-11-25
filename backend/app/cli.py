import argparse

from psycopg import sql

from app import aws, db

KEY_TAGS = ["owner", "team", "environment", "data-classification", "customer-facing"]


def tables() -> None:
    with db.connect() as conn:
        for t in db.TABLES:
            n = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))).fetchone()["n"]
            print(f"{t:<24}{n}")


def list_resources() -> list[dict]:
    s = aws.session()
    resources = []

    lam = s.client("lambda")
    for fn in lam.list_functions()["Functions"]:
        resources.append({"service": "lambda", "name": fn["FunctionName"],
                          "tags": lam.list_tags(Resource=fn["FunctionArn"])["Tags"]})

    bedrock = s.client("bedrock-agent")
    for summary in bedrock.list_agents()["agentSummaries"]:
        arn = bedrock.get_agent(agentId=summary["agentId"])["agent"]["agentArn"]
        resources.append({"service": "bedrock-agent", "name": summary["agentName"],
                          "tags": bedrock.list_tags_for_resource(resourceArn=arn)["tags"]})

    sm = s.client("sagemaker")
    for ep in sm.list_endpoints()["Endpoints"]:
        tags = sm.list_tags(ResourceArn=ep["EndpointArn"])["Tags"]
        resources.append({"service": "sagemaker", "name": ep["EndpointName"],
                          "tags": {t["Key"]: t["Value"] for t in tags}})

    sfn = s.client("stepfunctions")
    for machine in sfn.list_state_machines()["stateMachines"]:
        tags = sfn.list_tags_for_resource(resourceArn=machine["stateMachineArn"])["tags"]
        resources.append({"service": "stepfunctions", "name": machine["name"],
                          "tags": {t["key"]: t["value"] for t in tags}})

    ecs = s.client("ecs")
    for arn in ecs.list_task_definitions()["taskDefinitionArns"]:
        td = ecs.describe_task_definition(taskDefinition=arn, include=["TAGS"])
        resources.append({"service": "ecs", "name": td["taskDefinition"]["family"],
                          "tags": {t["key"]: t["value"] for t in td.get("tags", [])}})
    return resources


def estate() -> None:
    with aws.simulated_aws():
        resources = list_resources()
        print(f"{'SERVICE':<15}{'NAME':<28}" + "".join(f"{t.upper():<21}" for t in KEY_TAGS))
        for r in resources:
            print(f"{r['service']:<15}{r['name']:<28}" + "".join(f"{r['tags'].get(t, '-'):<21}" for t in KEY_TAGS))
        print(f"{len(resources)} resources\n")

        logs = aws.session().client("logs")
        groups = [g["logGroupName"] for g in logs.describe_log_groups()["logGroups"]]
        print(f"{'LOG GROUP':<40}SPANS")
        for group in groups:
            pages = logs.get_paginator("filter_log_events").paginate(logGroupName=group)
            print(f"{group:<40}{sum(len(p['events']) for p in pages)}")
        print(f"{len(groups)} log groups")


def main() -> None:
    commands = {"init": db.init_schema, "reset": db.reset_data, "tables": tables, "estate": estate}
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("cmd", choices=commands)
    commands[parser.parse_args().cmd]()


if __name__ == "__main__":
    main()
