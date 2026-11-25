import json
import os

import boto3

MODEL_ID = os.environ["MODEL_ID"]
bedrock = boto3.client("bedrock-runtime")

SYSTEM_PROMPT = (
    "You are the Northwind Retail help assistant. Answer questions about orders, shipping and returns "
    "in a friendly, concise way. If you are unsure, offer to connect the customer with a human agent."
)


def lambda_handler(event, context):
    body = json.loads(event["body"])
    history = body.get("history", [])
    messages = history + [{"role": "user", "content": body["message"]}]

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        }),
    )
    reply = json.loads(response["body"].read())["content"][0]["text"]
    return {"statusCode": 200, "body": json.dumps({"reply": reply})}
