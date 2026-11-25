import argparse

from psycopg import sql

from app import db


def tables() -> None:
    with db.connect() as conn:
        for t in db.TABLES:
            n = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))).fetchone()["n"]
            print(f"{t:<24}{n}")


def main() -> None:
    commands = {"init": db.init_schema, "reset": db.reset_data, "tables": tables}
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("cmd", choices=commands)
    commands[parser.parse_args().cmd]()


if __name__ == "__main__":
    main()
