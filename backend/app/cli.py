import argparse
from collections import Counter

from psycopg import sql

from app import aws, db, store
from app.config import config
from app.crawler.classifier import classify
from app.crawler.collectors import collect_all
from app.pipeline import run_discovery

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


def classify_estate() -> None:
    with aws.simulated_aws():
        resources, errors = collect_all(aws.session(), aws.lambda_code_fetcher("mock"))
    results = [(r, classify(r)) for r in resources]
    print(f"{'NAME':<28}{'AI':<7}{'CATEGORY':<19}{'SCORE':>6}  {'TASK':<20}EVIDENCE")
    for r, c in results:
        top = sorted(c.evidence, key=lambda e: e.weight, reverse=True)[:2]
        print(f"{r.name:<28}{str(c.is_ai):<7}{c.category or '-':<19}{c.agent_score:>6.2f}  {c.task_type or '-':<20}"
              + "; ".join(f"{e.signal}({e.detail})" for e in top))
    ai = sum(c.is_ai for _, c in results)
    print(f"{ai} AI, {sum(c.is_agent for _, c in results)} agents, {len(results) - ai} skipped")
    for error in errors:
        print(f"collector error: {error}")


def _discover(until):
    db.init_schema()  # idempotent, so a fresh database works without `cli init`
    with db.connect() as conn:
        return run_discovery(conn, config.discovery_mode, until)


def _print_crawl(summary):
    print(f"run {summary['run_id']}: {summary['resources_scanned']} resources scanned, "
          f"{summary['ai_systems_found']} AI systems, {summary['agents_found']} agents, {summary['retired']} retired")
    for error in summary["errors"]:
        print(f"collector error: {error}")


def crawl() -> None:
    _print_crawl(_discover("crawl")["crawl"])


def show() -> None:
    with db.connect() as conn:
        rows = store.list_registry(conn)
    print(f"{'NAME':<28}{'CATEGORY':<19}{'RISK':<10}{'AUTONOMY':<19}{'OVERSIGHT':<15}{'OWNER':<14}STATUS")
    for r in rows:
        print(f"{r['name']:<28}{r['category']:<19}{r['risk_level'] + ' ' + str(r['risk_score']):<10}"
              f"{r['autonomy_level']:<19}{r['human_oversight']:<15}{r['owner'] or '-':<14}{r['status']}")
    print(f"{len(rows)} AI systems")


def observe() -> None:
    result = _discover("observe")
    summary = result["observe"]
    print(f"run {result['run_id']}: {summary['spans']} spans")
    for agent, n in summary["spans_per_agent"].items():
        print(f"  {agent:<28}{n}")
    print(f"\n{'SEVERITY':<10}{'AGENT':<24}{'RULE':<30}DESCRIPTION")
    for f in summary["findings"]:
        print(f"{f['severity']:<10}{f['agent_name']:<24}{f['rule']:<30}{f['description']}")
    print(f"{len(summary['findings'])} findings")


def recommend() -> None:
    results = _discover("recommend")["recommendations"]
    for name, rec in results:
        cost = "-" if rec["est_monthly_cost_current"] is None else \
            f"${rec['est_monthly_cost_current']:,.2f} -> ${rec['est_monthly_cost_recommended']:,.2f}/mo"
        print(f"{name}\n  {rec['current_model']} -> {rec['recommended_model']}   {cost}\n  {rec['rationale']}")
    print(f"{len(results)} recommendations")


def discover() -> None:
    result = _discover("recommend")
    _print_crawl(result["crawl"])
    findings = Counter(f["severity"] for f in result["observe"]["findings"])
    print(f"{result['observe']['spans']} spans observed, {sum(findings.values())} findings "
          f"({', '.join(f'{n} {sev}' for sev, n in sorted(findings.items()))})")
    recs = [rec for _, rec in result["recommendations"]]
    current = sum(r["est_monthly_cost_current"] or 0 for r in recs)
    recommended = sum(r["est_monthly_cost_recommended"] or 0 for r in recs)
    print(f"{len(recs)} recommendations: ${current:,.2f}/mo now, ${recommended:,.2f}/mo recommended")


def main() -> None:
    commands = {"init": db.init_schema, "reset": db.reset_data, "tables": tables, "estate": estate, "collect": collect,
                "classify": classify_estate, "crawl": crawl, "show": show, "observe": observe,
                "recommend": recommend, "discover": discover}
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("cmd", choices=commands)
    commands[parser.parse_args().cmd]()


if __name__ == "__main__":
    main()
