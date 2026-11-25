import argparse

from psycopg import sql

from app import aws, db
from app.crawler.collectors import collect_all

KEY_TAGS = ["owner", "team", "environment", "data-classification", "customer-facing"]


def tables() -> None:
    with db.connect() as conn:
        for t in db.TABLES:
            n = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))).fetchone()["n"]
            print(f"{t:<24}{n}")


def estate() -> None:
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
        print(f"{'SERVICE':<15}{'NAME':<28}" + "".join(f"{t.upper():<21}" for t in KEY_TAGS))
        for r in resources:
            print(f"{r.service:<15}{r.name:<28}" + "".join(f"{r.tags.get(t, '-'):<21}" for t in KEY_TAGS))
        print(f"{len(resources)} resources")
        for error in errors:
            print(f"collector error: {error}")
        print()

        logs = aws.session().client("logs")
        groups = [g["logGroupName"] for g in logs.describe_log_groups()["logGroups"]]
        print(f"{'LOG GROUP':<40}SPANS")
        for group in groups:
            pages = logs.get_paginator("filter_log_events").paginate(logGroupName=group)
            print(f"{group:<40}{sum(len(p['events']) for p in pages)}")
        print(f"{len(groups)} log groups")


def collect() -> None:
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    print(f"{'SERVICE':<15}{'NAME':<28}{'IAM':>5}{'CODE':>6}  {'LOG GROUPS':<36}ERRORS")
    for r in resources:
        print(f"{r.service:<15}{r.name:<28}{len(r.iam_actions):>5}{len(r.code):>6}  "
              f"{', '.join(r.log_groups) or '-':<36}{'; '.join(r.errors) or '-'}")
    print(f"{len(resources)} resources")
    for error in errors:
        print(f"collector error: {error}")


def main() -> None:
    commands = {"init": db.init_schema, "reset": db.reset_data, "tables": tables, "estate": estate, "collect": collect}
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("cmd", choices=commands)
    commands[parser.parse_args().cmd]()


if __name__ == "__main__":
    main()
