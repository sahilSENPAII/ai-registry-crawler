import json
import os

import boto3

from orders import get_order, create_refund
from notifications import send_customer_email

MODEL_ID = os.environ["MODEL_ID"]
SESSION_TABLE = os.environ["SESSION_TABLE"]
APPROVAL_THRESHOLD = float(os.environ.get("APPROVAL_THRESHOLD", "500"))
MAX_ITERATIONS = 8

bedrock = boto3.client("bedrock-runtime")
sessions = boto3.resource("dynamodb").Table(SESSION_TABLE)

SYSTEM_PROMPT = (
    "You handle refund requests for Northwind Retail customers. Look up the order, "
    "decide whether a refund is due under the returns policy, issue it, and email the customer."
)

TOOL_CONFIG = {
    "tools": [
        {"toolSpec": {
            "name": "lookup_order",
            "description": "Fetch an order and its line items by order id.",
            "inputSchema": {"json": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
        }},
        {"toolSpec": {
            "name": "issue_refund",
            "description": "Refund an amount to the customer's original payment method.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "order_id": {"type": "string"}, "amount": {"type": "number"}, "reason": {"type": "string"},
            }, "required": ["order_id", "amount"]}},
        }},
        {"toolSpec": {
            "name": "send_email",
            "description": "Send an email to the customer on the order.",
            "inputSchema": {"json": {"type": "object", "properties": {
                "order_id": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            }, "required": ["order_id", "subject", "body"]}},
        }},
    ]
}


def run_tool(name, args):
    if name == "lookup_order":
        return get_order(args["order_id"])
    if name == "issue_refund":
        if args["amount"] > APPROVAL_THRESHOLD:
            return {"status": "pending_approval", "threshold": APPROVAL_THRESHOLD}
        return create_refund(args["order_id"], args["amount"], args.get("reason", ""))
    if name == "send_email":
        return send_customer_email(args["order_id"], args["subject"], args["body"])
    raise ValueError(f"unknown tool {name}")


def load_history(session_id):
    item = sessions.get_item(Key={"session_id": session_id}).get("Item")
    return json.loads(item["messages"]) if item else []


def save_history(session_id, messages):
    sessions.put_item(Item={"session_id": session_id, "messages": json.dumps(messages)})


def lambda_handler(event, context):
    session_id = event["session_id"]
    messages = load_history(session_id)
    messages.append({"role": "user", "content": [{"text": event["message"]}]})

    response = bedrock.converse(
        modelId=MODEL_ID, system=[{"text": SYSTEM_PROMPT}], messages=messages, toolConfig=TOOL_CONFIG,
    )
    iterations = 0
    while response["stopReason"] == "tool_use" and iterations < MAX_ITERATIONS:
        iterations += 1
        message = response["output"]["message"]
        messages.append(message)
        results = []
        for block in message["content"]:
            if "toolUse" in block:
                use = block["toolUse"]
                output = run_tool(use["name"], use["input"])
                results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": [{"json": output}]}})
        messages.append({"role": "user", "content": results})
        response = bedrock.converse(
            modelId=MODEL_ID, system=[{"text": SYSTEM_PROMPT}], messages=messages, toolConfig=TOOL_CONFIG,
        )

    messages.append(response["output"]["message"])
    save_history(session_id, messages)
    return {"reply": response["output"]["message"]["content"][0]["text"]}
