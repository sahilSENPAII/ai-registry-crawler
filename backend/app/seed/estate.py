import io
import json
import zipfile
from pathlib import Path

from app.aws import session
from app.config import config

LAMBDA_SRC = Path(__file__).with_name("lambda_src")
SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"


def _tags(owner, team, environment, data_classification, customer_facing):
    return {
        "owner": owner,
        "team": team,
        "environment": environment,
        "data-classification": data_classification,
        "customer-facing": customer_facing,
    }


def _zip(src_dir):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for path in sorted((LAMBDA_SRC / src_dir).glob("*.py")):
            zf.write(path, path.name)
    return buffer.getvalue()


def _create_role(iam, name, service, actions):
    trust = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Principal": {"Service": service}, "Action": "sts:AssumeRole"},
    ]}
    policy = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": actions, "Resource": "*"}]}
    arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-policy", PolicyDocument=json.dumps(policy))
    return arn


def _lambdas(region, account):
    openai_secret = f"arn:aws:secretsmanager:{region}:{account}:secret:prod/openai/api-key-Q7mT2x"
    return [
        {
            "name": "refund-agent", "src": "refund_agent",
            "env": {"MODEL_ID": SONNET, "SESSION_TABLE": "refund-sessions", "APPROVAL_THRESHOLD": "500"},
            "actions": ["bedrock:InvokeModel", "dynamodb:GetItem", "dynamodb:PutItem", "ses:SendEmail"],
            "tags": _tags("maria.chen", "customer-care", "prod", "pii", "true"),
        },
        {
            "name": "sales-research-agent", "src": "sales_research_agent",
            "env": {"OPENAI_MODEL": "gpt-4o", "OPENAI_API_KEY_SECRET_ARN": openai_secret,
                    "CRM_BASE_URL": "https://northwind.my.salesforce.com"},
            "actions": ["secretsmanager:GetSecretValue"],
            "tags": _tags("dev.patel", "sales-ops", "prod", "confidential", "false"),
        },
        {
            "name": "support-chatbot", "src": "support_chatbot",
            "env": {"MODEL_ID": HAIKU},
            "actions": ["bedrock:InvokeModel"],
            "tags": _tags("maria.chen", "customer-care", "prod", "confidential", "true"),
        },
        {
            "name": "invoice-summarizer", "src": "invoice_summarizer",
            "env": {"MODEL_ID": SONNET, "SUMMARY_TABLE": "invoice-summaries"},
            "actions": ["s3:GetObject", "bedrock:InvokeModel", "dynamodb:PutItem"],
            "tags": _tags("li.wong", "finance", "prod", "confidential", "false"),
        },
        {
            "name": "contract-review-rag", "src": "contract_review_rag",
            "env": {"KNOWLEDGE_BASE_ID": "KB7XQ2LMNP",
                    "MODEL_ARN": f"arn:aws:bedrock:{region}::foundation-model/anthropic.claude-3-sonnet-20240229-v1:0"},
            "actions": ["bedrock:RetrieveAndGenerate", "bedrock:Retrieve", "bedrock:InvokeModel"],
            "tags": _tags("sam.okafor", "legal", "prod", "confidential", "false"),
        },
        {
            "name": "ticket-classifier", "src": "ticket_classifier",
            "env": {"OPENAI_MODEL": "gpt-4o-mini", "OPENAI_API_KEY_SECRET_ARN": openai_secret,
                    "TICKETS_TABLE": "support-tickets"},
            "actions": ["secretsmanager:GetSecretValue", "dynamodb:UpdateItem", "sqs:ReceiveMessage", "sqs:DeleteMessage"],
            "tags": _tags("maria.chen", "customer-care", "prod", "pii", "false"),
        },
        {
            "name": "image-thumbnailer", "src": "image_thumbnailer",
            "env": {"OUTPUT_BUCKET": "northwind-product-thumbnails"},
            "actions": ["s3:GetObject", "s3:PutObject"],
            "tags": _tags("jo.martin", "ecommerce", "prod", "public", "false"),
        },
        {
            "name": "nightly-billing-export", "src": "nightly_billing_export",
            "env": {"EXPORT_BUCKET": "northwind-finance-exports",
                    "DB_HOST": f"billing-db.cluster-c9x2kq.{region}.rds.amazonaws.com",
                    "DB_SECRET_ARN": f"arn:aws:secretsmanager:{region}:{account}:secret:prod/billing-db-Lp4R8n"},
            "actions": ["secretsmanager:GetSecretValue", "s3:PutObject", "rds-db:connect"],
            "tags": _tags("li.wong", "finance", "prod", "confidential", "false"),
        },
    ]


def _loan_definition(region, account):
    return {
        "Comment": "Underwrite consumer loan applications",
        "StartAt": "FetchCreditReport",
        "States": {
            "FetchCreditReport": {
                "Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {"FunctionName": "credit-bureau-lookup", "Payload.$": "$"},
                "ResultSelector": {"report.$": "$.Payload"}, "ResultPath": "$.credit", "Next": "AssessApplication",
            },
            "AssessApplication": {
                "Type": "Task", "Resource": "arn:aws:states:::bedrock:invokeModel",
                "Parameters": {
                    "ModelId": SONNET,
                    "Body": {
                        "anthropic_version": "bedrock-2023-05-31",
                        "max_tokens": 800,
                        "messages": [{"role": "user", "content": [{"type": "text", "text.$": (
                            "States.Format('Assess this loan application against the underwriting policy "
                            "and recommend approve, decline or refer: {}', States.JsonToString($))"
                        )}]}],
                    },
                },
                "ResultSelector": {"recommendation.$": "$.Body.content[0].text"},
                "ResultPath": "$.assessment", "Next": "UnderwriterReview",
            },
            "UnderwriterReview": {
                "Type": "Task", "Resource": "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
                "Parameters": {
                    "QueueUrl": f"https://sqs.{region}.amazonaws.com/{account}/underwriter-review",
                    "MessageBody": {"application.$": "$", "taskToken.$": "$$.Task.Token"},
                },
                "ResultPath": "$.review", "Next": "RecordDecision",
            },
            "RecordDecision": {
                "Type": "Task", "Resource": "arn:aws:states:::dynamodb:putItem",
                "Parameters": {"TableName": "loan-decisions", "Item": {
                    "application_id": {"S.$": "$.application_id"}, "decision": {"S.$": "$.review.decision"},
                }},
                "End": True,
            },
        },
    }


def _order_definition(region, account):
    return {
        "Comment": "Reserve stock, take payment and confirm the order",
        "StartAt": "ReserveInventory",
        "States": {
            "ReserveInventory": {
                "Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {"FunctionName": "inventory-reserve", "Payload.$": "$"},
                "ResultPath": "$.reservation", "Next": "ChargePayment",
            },
            "ChargePayment": {
                "Type": "Task", "Resource": "arn:aws:states:::lambda:invoke",
                "Parameters": {"FunctionName": "payment-charge", "Payload.$": "$"},
                "Retry": [{"ErrorEquals": ["States.TaskFailed"], "MaxAttempts": 2}],
                "ResultPath": "$.payment", "Next": "RecordOrder",
            },
            "RecordOrder": {
                "Type": "Task", "Resource": "arn:aws:states:::dynamodb:putItem",
                "Parameters": {"TableName": "orders", "Item": {
                    "order_id": {"S.$": "$.order_id"}, "status": {"S": "confirmed"},
                }},
                "ResultPath": None, "Next": "NotifyCustomer",
            },
            "NotifyCustomer": {
                "Type": "Task", "Resource": "arn:aws:states:::sns:publish",
                "Parameters": {"TopicArn": f"arn:aws:sns:{region}:{account}:order-confirmations", "Message.$": "$.order_id"},
                "End": True,
            },
        },
    }


def seed_estate():
    s = session()
    region = s.region_name
    account = s.client("sts").get_caller_identity()["Account"]
    iam = s.client("iam")
    prefix = config.agent_log_prefix

    lambdas = _lambdas(region, account)
    lambda_roles = {
        fn["name"]: _create_role(iam, f"{fn['name']}-role", "lambda.amazonaws.com", fn["actions"])
        for fn in lambdas
    }
    hr_agent_role = _create_role(iam, "AmazonBedrockExecutionRoleForAgents_hr", "bedrock.amazonaws.com",
                                 ["bedrock:InvokeModel", "bedrock:Retrieve"])
    sagemaker_role = _create_role(iam, "sagemaker-execution-role", "sagemaker.amazonaws.com",
                                  ["s3:GetObject", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "logs:PutLogEvents"])
    # shared by both state machines, so order-fulfilment can call Bedrock without ever doing so
    workflow_role = _create_role(iam, "workflow-execution-role", "states.amazonaws.com",
                                 ["lambda:InvokeFunction", "bedrock:InvokeModel", "sqs:SendMessage",
                                  "dynamodb:PutItem", "sns:Publish"])
    procurement_role = _create_role(iam, "procurement-agent-task-role", "ecs-tasks.amazonaws.com",
                                    ["bedrock:InvokeModel", "elasticache:Connect", "dynamodb:PutItem"])
    storefront_role = _create_role(iam, "web-storefront-task-role", "ecs-tasks.amazonaws.com",
                                   ["dynamodb:GetItem", "dynamodb:Query", "s3:GetObject"])

    lam = s.client("lambda")
    for fn in lambdas:
        lam.create_function(
            FunctionName=fn["name"], Runtime="python3.12", Handler="handler.lambda_handler",
            Role=lambda_roles[fn["name"]], Code={"ZipFile": _zip(fn["src"])},
            Environment={"Variables": fn["env"]}, Tags=fn["tags"], Timeout=60, MemorySize=512,
        )

    s.client("bedrock-agent").create_agent(
        agentName="hr-policy-assistant",
        foundationModel="anthropic.claude-3-sonnet-20240229-v1:0",
        agentResourceRoleArn=hr_agent_role,
        description="Answers employee HR policy questions and pre-screens leave and accommodation requests.",
        instruction=(
            "You help Northwind employees with HR policy. Answer from the employee handbook, check leave balances, "
            "and decide whether leave or accommodation requests meet policy before they go to an HR partner."
        ),
        # moto has no action groups or knowledge-base associations, so these live in tags
        tags={
            **_tags("aisha.rahman", "people-ops", "prod", "pii", "false"),
            "ai:declared-tools": "lookup_policy get_leave_balance submit_leave_request",
            "ai:knowledge-base": "hr-handbook-kb",
            "ai:log-group": f"{prefix}hr-policy-assistant",
        },
    )

    sm = s.client("sagemaker")
    endpoints = [
        ("churn-predictor", f"683313688378.dkr.ecr.{region}.amazonaws.com/sagemaker-xgboost:1.7-1",
         _tags("nina.berg", "data-science", "prod", "confidential", "false")),
        ("fraud-scoring", f"763104351884.dkr.ecr.{region}.amazonaws.com/pytorch-inference:2.1.0-cpu-py310",
         _tags("omar.haddad", "payments-risk", "prod", "pii", "false")),
    ]
    for name, image, tags in endpoints:
        sm.create_model(
            ModelName=name, ExecutionRoleArn=sagemaker_role,
            PrimaryContainer={"Image": image, "ModelDataUrl": f"s3://northwind-ml-models/{name}/model.tar.gz"},
        )
        sm.create_endpoint_config(EndpointConfigName=f"{name}-config", ProductionVariants=[{
            "VariantName": "primary", "ModelName": name, "InstanceType": "ml.m5.large", "InitialInstanceCount": 1,
        }])
        sm.create_endpoint(EndpointName=name, EndpointConfigName=f"{name}-config",
                           Tags=[{"Key": k, "Value": v} for k, v in tags.items()])

    sfn = s.client("stepfunctions")
    workflows = [
        ("loan-underwriting-workflow", _loan_definition(region, account),
         _tags("grace.kim", "lending", "prod", "pii", "false")),
        ("order-fulfilment", _order_definition(region, account),
         _tags("jo.martin", "ecommerce", "prod", "confidential", "false")),
    ]
    for name, definition, tags in workflows:
        sfn.create_state_machine(name=name, definition=json.dumps(definition), roleArn=workflow_role,
                                 tags=[{"key": k, "value": v} for k, v in tags.items()])

    ecs = s.client("ecs")
    ecs.register_task_definition(
        family="procurement-agent", taskRoleArn=procurement_role, networkMode="awsvpc", cpu="1024", memory="2048",
        containerDefinitions=[{
            "name": "agent",
            "image": f"{account}.dkr.ecr.{region}.amazonaws.com/procurement-agent:1.4.2",
            "essential": True,
            "environment": [
                {"name": "AGENT_FRAMEWORK", "value": "crewai"},
                {"name": "MODEL_ID", "value": SONNET},
                {"name": "MEMORY_BACKEND", "value": "redis"},
                {"name": "REDIS_URL", "value": "redis://procurement-memory.x1y2z3.0001.cache.amazonaws.com:6379"},
                {"name": "TOOLS", "value": "search_suppliers,create_purchase_order"},
            ],
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": f"{prefix}procurement-agent", "awslogs-region": region, "awslogs-stream-prefix": "agent",
            }},
        }],
        tags=[{"key": k, "value": v} for k, v in _tags("felix.braun", "procurement", "prod", "confidential", "false").items()],
    )
    ecs.register_task_definition(
        family="web-storefront", taskRoleArn=storefront_role, networkMode="awsvpc", cpu="512", memory="1024",
        containerDefinitions=[
            {"name": "nginx", "image": "nginx:1.25-alpine", "essential": True,
             "portMappings": [{"containerPort": 80}]},
            {"name": "app", "image": f"{account}.dkr.ecr.{region}.amazonaws.com/web-storefront:3.8.0", "essential": True,
             "environment": [
                 {"name": "NODE_ENV", "value": "production"},
                 {"name": "CATALOG_TABLE", "value": "product-catalog"},
                 {"name": "PORT", "value": "3000"},
             ]},
        ],
        tags=[{"key": k, "value": v} for k, v in _tags("jo.martin", "ecommerce", "prod", "public", "true").items()],
    )

    logs = s.client("logs")
    for agent in ["refund-agent", "sales-research-agent", "hr-policy-assistant", "procurement-agent"]:
        logs.create_log_group(logGroupName=f"{prefix}{agent}")
