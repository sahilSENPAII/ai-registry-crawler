import json
import os

import boto3
from openai import OpenAI

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
LABELS = ["billing", "shipping", "returns", "account", "product_question", "other"]

secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["OPENAI_API_KEY_SECRET_ARN"])
client = OpenAI(api_key=secret["SecretString"])
tickets = boto3.resource("dynamodb").Table(os.environ["TICKETS_TABLE"])


def lambda_handler(event, context):
    for record in event["Records"]:
        ticket = json.loads(record["body"])
        response = client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": f"Classify the support ticket. Reply with exactly one of: {', '.join(LABELS)}."},
                {"role": "user", "content": f"Subject: {ticket['subject']}\n\n{ticket['body']}"},
            ],
        )
        label = response.choices[0].message.content.strip().lower()
        if label not in LABELS:
            label = "other"
        tickets.update_item(
            Key={"ticket_id": ticket["id"]},
            UpdateExpression="SET queue = :q",
            ExpressionAttributeValues={":q": label},
        )
