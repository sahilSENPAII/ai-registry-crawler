"""Hard, labelled resources for `cli evaluate`: (resource, (is_ai, is_agent), why it is hard).
Generated in memory, never deployed; only the evaluation reads them."""
from app.crawler.collectors import Resource

ACCOUNT, REGION = "123456789012", "us-east-1"
HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"


def _lambda(name, code, env=None, actions=()):
    return Resource("lambda", name, f"arn:aws:lambda:{REGION}:{ACCOUNT}:function:{name}", ACCOUNT, REGION,
                    env=env or {}, config={"runtime": "python3.12", "handler": "handler.lambda_handler", "description": ""},
                    code={"handler.py": code}, iam_actions=list(actions))


def _ecs(name, image, env):
    return Resource("ecs", name, f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{name}:1", ACCOUNT, REGION, env=env,
                    config={"revision": 1, "containers": [{"name": "app", "image": image, "log_configuration": None}]})


def _workflow(name, definition):
    return Resource("stepfunctions", name, f"arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{name}", ACCOUNT, REGION,
                    config={"type": "STANDARD", "definition": definition}, iam_actions=["lambda:InvokeFunction"])


AGENT = (True, True)
AI = (True, False)
NOT_AI = (False, False)

VARIANTS = [
    (_lambda("tools-in-comment", f'''import boto3

bedrock = boto3.client("bedrock-runtime")


def lambda_handler(event, context):
    # TODO: pass tools=ORDER_TOOLS here once the order API is ready, so it can look orders up itself
    response = bedrock.converse(modelId="{HAIKU}", messages=[{{"role": "user", "content": [{{"text": event["question"]}}]}}])
    return {{"answer": response["output"]["message"]["content"][0]["text"]}}
''', actions=["bedrock:InvokeModel"]), AI, "one model call; tools= appears only in a comment"),

    (_lambda("unused-framework-import", '''import json

import boto3
import langchain  # left over from the old prompt helpers

s3 = boto3.client("s3")


def lambda_handler(event, context):
    rows = [f"{r['sku']},{r['qty']}" for r in event["rows"]]
    s3.put_object(Bucket="stock-reports", Key=event["day"] + ".csv", Body="\\n".join(rows))
    return {"rows": len(rows)}
''', actions=["s3:PutObject"]), NOT_AI, "imports an agent framework but never uses it"),

    (_lambda("job-dispatcher-loop", '''import json

import boto3

sqs = boto3.client("sqs")
HANDLERS = {"resize": lambda a: a, "archive": lambda a: a}


def lambda_handler(event, context):
    job = json.loads(event["Records"][0]["body"])
    while job.get("tool_calls"):
        call = job["tool_calls"].pop(0)
        HANDLERS[call["name"]](call["arguments"])
    return {"done": True}
''', actions=["sqs:ReceiveMessage"]), NOT_AI, "a loop over tool_calls with no model call"),

    (_lambda("langgraph-react-agent", f'''from langchain_aws import ChatBedrockConverse
from langgraph.prebuilt import create_react_agent

from orders import cancel_order, lookup_order

model = ChatBedrockConverse(model="{HAIKU}")
agent = create_react_agent(model, [lookup_order, cancel_order])


def lambda_handler(event, context):
    result = agent.invoke({{"messages": [("user", event["message"])]}})
    return {{"reply": result["messages"][-1].content}}
''', actions=["bedrock:InvokeModel"]), AGENT, "LangGraph agent with tools passed positionally, no tools= kwarg"),

    (_lambda("agent-consumer", '''import uuid

import boto3

agents = boto3.client("bedrock-agent-runtime")


def lambda_handler(event, context):
    response = agents.invoke_agent(agentId="HRPOLICY01", agentAliasId="PROD", sessionId=str(uuid.uuid4()),
                                   inputText=event["question"])
    return {"answer": "".join(e["chunk"]["bytes"].decode() for e in response["completion"] if "chunk" in e)}
''', actions=["bedrock:InvokeAgent"]), AI, "calls a Bedrock agent through invoke_agent; a consumer, not an agent"),

    (_workflow("refund-escalation", {
        "Comment": "Send escalated refund requests to the refund agent, then notify the team",
        "StartAt": "AskRefundAgent",
        "States": {
            "AskRefundAgent": {"Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                               "Parameters": {"FunctionName": "refund-agent", "Payload.$": "$"}, "Next": "Notify"},
            "Notify": {"Type": "Task", "Resource": "arn:aws:states:::sns:publish",
                       "Parameters": {"TopicArn": f"arn:aws:sns:{REGION}:{ACCOUNT}:refunds", "Message.$": "$.Payload"},
                       "End": True},
        },
    }), NOT_AI, "a workflow that invokes an agent Lambda but calls no model itself"),

    (_lambda("openai-assistants-agent", '''import os

from openai import OpenAI

from billing import refund_invoice

client = OpenAI()
ASSISTANT = client.beta.assistants.create(model="gpt-4o", instructions="Resolve billing disputes.", tools=[
    {"type": "function", "function": {"name": "refund_invoice", "parameters": {"type": "object"}}},
])


def lambda_handler(event, context):
    thread = client.beta.threads.create(messages=[{"role": "user", "content": event["message"]}])
    run = client.beta.threads.runs.create_and_poll(thread_id=thread.id, assistant_id=ASSISTANT.id)
    while run.status == "requires_action":
        outputs = [{"tool_call_id": c.id, "output": refund_invoice(c.function.arguments)}
                   for c in run.required_action.submit_tool_outputs.tool_calls]
        run = client.beta.threads.runs.submit_tool_outputs_and_poll(thread_id=thread.id, run_id=run.id, tool_outputs=outputs)
    return {"status": run.status}
''', {"OPENAI_API_KEY": "[masked]"}), AGENT, "OpenAI Assistants agent; no chat.completions call"),

    (_lambda("anthropic-tool-loop", '''import anthropic

from crm import TOOLS, run_tool

client = anthropic.Anthropic()


def lambda_handler(event, context):
    messages = [{"role": "user", "content": event["message"]}]
    response = client.messages.create(model="claude-3-5-haiku-latest", max_tokens=1024, tools=TOOLS, messages=messages)
    while response.stop_reason == "tool_use":
        messages.append({"role": "assistant", "content": response.content})
        results = [{"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)}
                   for b in response.content if b.type == "tool_use"]
        messages.append({"role": "user", "content": results})
        response = client.messages.create(model="claude-3-5-haiku-latest", max_tokens=1024, tools=TOOLS, messages=messages)
    return {"reply": response.content[0].text}
''', {"ANTHROPIC_API_KEY": "[masked]"}), AGENT, "Anthropic SDK tool-use loop"),

    (_lambda("structured-extraction", f'''import boto3

bedrock = boto3.client("bedrock-runtime")
EXTRACT = {{"tools": [{{"toolSpec": {{"name": "invoice_fields", "inputSchema": {{"json": {{"type": "object"}}}}}}}}],
           "toolChoice": {{"tool": {{"name": "invoice_fields"}}}}}}


def lambda_handler(event, context):
    response = bedrock.converse(modelId="{HAIKU}", toolConfig=EXTRACT,
                                messages=[{{"role": "user", "content": [{{"text": event["invoice_text"]}}]}}])
    return response["output"]["message"]["content"][0]["toolUse"]["input"]
''', actions=["bedrock:InvokeModel"]), AI, "one forced tool call used for structured output, no loop"),

    (_lambda("stale-model-env", '''import csv
import io


def lambda_handler(event, context):
    rows = list(csv.DictReader(io.StringIO(event["csv"])))
    return {"rows": len(rows), "columns": list(rows[0]) if rows else []}
''', {"MODEL_ID": HAIKU, "MAX_ROWS": "5000"}), NOT_AI, "a leftover MODEL_ID env var; the code never calls a model"),

    (_lambda("raw-http-openai", '''import json
import os
import urllib.request


def lambda_handler(event, context):
    body = json.dumps({"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Translate to French: " + event["text"]}]})
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=body.encode(), headers={
        "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return {"text": json.loads(resp.read())["choices"][0]["message"]["content"]}
''', {"OPENAI_API_KEY": "[masked]"}), AI, "calls the OpenAI API over raw HTTP, no SDK"),

    (_lambda("review-sentiment", '''import boto3

comprehend = boto3.client("comprehend")


def lambda_handler(event, context):
    result = comprehend.detect_sentiment(Text=event["review"], LanguageCode="en")
    return {"sentiment": result["Sentiment"]}
''', actions=["comprehend:DetectSentiment"]), AI, "AWS AI service (Comprehend)"),

    (_lambda("receipt-ocr", '''import boto3

textract = boto3.client("textract")


def lambda_handler(event, context):
    doc = {"S3Object": {"Bucket": event["bucket"], "Name": event["key"]}}
    result = textract.analyze_expense(Document=doc)
    return {"fields": len(result["ExpenseDocuments"][0]["SummaryFields"])}
''', actions=["textract:AnalyzeExpense", "s3:GetObject"]), AI, "AWS AI service (Textract)"),

    (_lambda("price-model-caller", '''import json

import boto3

runtime = boto3.client("sagemaker-runtime")


def lambda_handler(event, context):
    response = runtime.invoke_endpoint(EndpointName="price-elasticity", ContentType="application/json",
                                       Body=json.dumps(event["features"]))
    return json.loads(response["Body"].read())
''', actions=["sagemaker:InvokeEndpoint"]), AI, "calls a SageMaker endpoint"),

    (_lambda("prompt-builder", '''import json

import boto3

sqs = boto3.client("sqs")
TEMPLATE = "You are a helpful assistant for Northwind. Summarise this ticket for the on-call engineer:\\n\\n{body}"


def lambda_handler(event, context):
    prompt = TEMPLATE.format(body=event["body"])
    sqs.send_message(QueueUrl=event["queue_url"], MessageBody=json.dumps({"prompt": prompt}))
    return {"queued": True}
''', actions=["sqs:SendMessage"]), NOT_AI, "builds a prompt and queues it for another service"),

    (_ecs("supplier-crew", f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/supplier-crew:2.0", {
        "OPENAI_MODEL_NAME": "gpt-4o", "CREW_TOOLS": "search_suppliers,create_purchase_order",
        "CREWAI_TELEMETRY_OPT_OUT": "true", "OPENAI_API_KEY": "[masked]",
    }), AGENT, "CrewAI agent on ECS without the AGENT_FRAMEWORK env var"),

    (_lambda("lcel-chain", f'''from langchain_aws import ChatBedrock
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

prompt = ChatPromptTemplate.from_template("Write a product description for: {{item}}")
chain = prompt | ChatBedrock(model_id="{HAIKU}") | StrOutputParser()


def lambda_handler(event, context):
    return {{"description": chain.invoke({{"item": event["item"]}})}}
''', actions=["bedrock:InvokeModel"]), AI, "LangChain chain with one model call, no tools"),

    (_lambda("telemetry-forwarder", '''import base64
import json

import boto3

logs = boto3.client("logs")


def lambda_handler(event, context):
    records = list(event["Records"])
    while records:
        span = json.loads(base64.b64decode(records.pop()["kinesis"]["data"]))
        if span["attributes"].get("gen_ai.tool_calls"):
            span["attributes"]["tool_call_count"] = len(span["attributes"]["gen_ai.tool_calls"])
        logs.put_log_events(logGroupName="/ai/agents/forwarded", logStreamName="all",
                            logEvents=[{"timestamp": span["ts"], "message": json.dumps(span)}])
''', actions=["logs:PutLogEvents"]), NOT_AI, "forwards other agents' gen_ai spans; makes no model call"),

    (_lambda("autogen-group-chat", '''from autogen import AssistantAgent, GroupChat, GroupChatManager, UserProxyAgent, register_function

from inventory import check_stock, reorder

config = {"config_list": [{"model": "gpt-4o", "api_type": "openai"}]}
planner = AssistantAgent("planner", llm_config=config)
buyer = AssistantAgent("buyer", llm_config=config)
proxy = UserProxyAgent("proxy", human_input_mode="NEVER")
register_function(check_stock, caller=buyer, executor=proxy, description="Check stock levels")
register_function(reorder, caller=buyer, executor=proxy, description="Place a reorder")


def lambda_handler(event, context):
    manager = GroupChatManager(GroupChat([planner, buyer, proxy], messages=[]), llm_config=config)
    proxy.initiate_chat(manager, message=event["request"])
''', {"OPENAI_API_KEY": "[masked]"}), AGENT, "AutoGen multi-agent chat with registered tools"),

    (_lambda("embedding-indexer", '''import json

import boto3

bedrock = boto3.client("bedrock-runtime")


def lambda_handler(event, context):
    out = []
    for doc in event["documents"]:
        response = bedrock.invoke_model(modelId="amazon.titan-embed-text-v2:0", body=json.dumps({"inputText": doc}))
        out.append(json.loads(response["body"].read())["embedding"])
    return {"vectors": len(out)}
''', actions=["bedrock:InvokeModel"]), AI, "embedding model calls in a plain loop"),

    (_lambda("pydantic-ai-agent", f'''from pydantic_ai import Agent

from orders import get_order, issue_refund

agent = Agent("bedrock:{SONNET}", tools=[get_order, issue_refund],
              system_prompt="Handle refund requests under the returns policy.")


def lambda_handler(event, context):
    return {{"reply": agent.run_sync(event["message"]).output}}
''', actions=["bedrock:InvokeModel"]), AGENT, "Pydantic AI agent, a framework the rules do not know"),

    (_lambda("strands-agent", f'''from strands import Agent, tool


@tool
def lookup_shipment(tracking_id: str) -> dict:
    """Find a shipment by tracking id."""
    return {{"tracking_id": tracking_id, "status": "in transit"}}


@tool
def reroute_shipment(tracking_id: str, depot: str) -> dict:
    """Send a shipment to another depot."""
    return {{"tracking_id": tracking_id, "depot": depot}}


agent = Agent(model="us.{SONNET}", tools=[lookup_shipment, reroute_shipment])


def lambda_handler(event, context):
    return {{"reply": str(agent(event["message"]))}}
''', actions=["bedrock:InvokeModel"]), AGENT, "Strands agent with @tool functions"),

    (_lambda("faq-bot", '''import re

ANSWERS = {r"refund|return": "Returns are free within 30 days.", r"ship|deliver": "Orders ship in 2 days."}


def lambda_handler(event, context):
    history = event.get("conversation", [])
    for pattern, answer in ANSWERS.items():
        if re.search(pattern, event["message"], re.I):
            return {"reply": answer, "conversation": history + [answer]}
    return {"reply": "Let me connect you with a person.", "conversation": history}
''', actions=[]), NOT_AI, "a keyword chatbot with conversation history and no model"),

    (_lambda("llm-gateway-client", '''import json
import os
import urllib.request


def lambda_handler(event, context):
    payload = {"model": "claude-3-haiku", "prompt": "Tag this product: " + event["title"], "max_tokens": 50}
    req = urllib.request.Request(os.environ["GATEWAY_URL"] + "/v1/complete", data=json.dumps(payload).encode())
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())
''', {"GATEWAY_URL": "https://llm-gateway.internal.northwind.example"}), AI,
     "calls a model through an internal gateway"),

    (_lambda("image-moderation", '''import boto3

rekognition = boto3.client("rekognition")


def lambda_handler(event, context):
    image = {"S3Object": {"Bucket": event["bucket"], "Name": event["key"]}}
    labels = rekognition.detect_moderation_labels(Image=image, MinConfidence=80)["ModerationLabels"]
    return {"flagged": bool(labels)}
''', actions=["rekognition:DetectModerationLabels"]), AI, "AWS AI service (Rekognition)"),

    (_lambda("sklearn-scorer", '''import json

import joblib

model = joblib.load("model.pkl")


def lambda_handler(event, context):
    score = model.predict_proba([event["features"]])[0][1]
    return {"churn_probability": float(score)}
''', actions=[]), AI, "serves a pickled scikit-learn model with no AI keywords"),
]
