import csv
import io
import os
from datetime import date, timedelta

import boto3
import pg8000.native

EXPORT_BUCKET = os.environ["EXPORT_BUCKET"]

s3 = boto3.client("s3")


def connect():
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])
    user, password = secret["SecretString"].split(":", 1)
    return pg8000.native.Connection(user, password=password, host=os.environ["DB_HOST"], database="billing")


def lambda_handler(event, context):
    day = date.today() - timedelta(days=1)
    conn = connect()
    rows = conn.run(
        "SELECT invoice_id, customer_id, amount_cents, currency, issued_at FROM invoices WHERE issued_at::date = :day",
        day=day,
    )
    conn.close()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["invoice_id", "customer_id", "amount_cents", "currency", "issued_at"])
    writer.writerows(rows)
    s3.put_object(Bucket=EXPORT_BUCKET, Key=f"billing/{day.isoformat()}.csv", Body=buffer.getvalue().encode())
    return {"rows": len(rows), "day": day.isoformat()}
