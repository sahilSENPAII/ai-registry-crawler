import json
import os

import boto3
from openai import OpenAI

from crm import find_account, add_note
from search import search_web

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")


def openai_client():
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["OPENAI_API_KEY_SECRET_ARN"])
    return OpenAI(api_key=secret["SecretString"])


client = openai_client()

TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the public web for recent news about a company.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "crm_lookup",
        "description": "Look up an account and its open opportunities in the CRM.",
        "parameters": {"type": "object", "properties": {"account_name": {"type": "string"}}, "required": ["account_name"]},
    }},
    {"type": "function", "function": {
        "name": "update_crm_note",
        "description": "Attach a research note to a CRM account.",
        "parameters": {"type": "object", "properties": {
            "account_id": {"type": "string"}, "note": {"type": "string"},
        }, "required": ["account_id", "note"]},
    }},
]

HANDLERS = {
    "web_search": lambda a: search_web(a["query"]),
    "crm_lookup": lambda a: find_account(a["account_name"]),
    "update_crm_note": lambda a: add_note(a["account_id"], a["note"]),
}


def lambda_handler(event, context):
    messages = [
        {"role": "system", "content": "You research prospects for the sales team and keep the CRM up to date."},
        {"role": "user", "content": f"Prepare a briefing on {event['account_name']} before tomorrow's call."},
    ]
    while True:
        response = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, tool_choice="auto")
        message = response.choices[0].message
        messages.append(message)
        if not message.tool_calls:
            return {"briefing": message.content}
        for call in message.tool_calls:
            result = HANDLERS[call.function.name](json.loads(call.function.arguments))
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
