import pytest
from moto import mock_aws

from app import aws
from app.crawler.collectors import collect_all
from app.crawler.masking import MASK, mask, mask_text
from app.seed.estate import seed_estate


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GITHUB_TOKEN", "CLIENT_SECRET", "DB_PASSWORD", "SMTP_PASSWD",
                                  "AWS_CREDENTIALS", "AUTH_HEADER", "apiKey"])
def test_secret_names_are_masked_and_kept(name):
    assert mask({name: "hunter2", "MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0"}) == \
        {name: MASK, "MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0"}


@pytest.mark.parametrize("secret", [
    "sk-proj-4fG7hK2mN9pQ1rS6tV8wX3yZ",                # OpenAI
    "sk-ant-api03-Zx8Vb2Nm4Qw6Er8Ty0Ui",               # Anthropic
    "AKIAIOSFODNN7EXAMPLE",                            # AWS access key id
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b",        # hex
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",        # base64, the shape of an AWS secret key
])
def test_secret_values_are_masked_under_any_name(secret):
    assert mask({"VALUE": secret}) == {"VALUE": MASK}
    assert mask_text(f"client = Client({secret!r})") == f"client = Client('{MASK}')"


def test_connection_string_keeps_user_and_host():
    assert mask({"DATABASE_URL": "postgresql://billing:s3cr3t-Pw@billing-db.internal:5432/billing"}) == \
        {"DATABASE_URL": f"postgresql://billing:{MASK}@billing-db.internal:5432/billing"}
    assert mask_text('redis.from_url("redis://:hunter2@cache:6379")') == f'redis.from_url("redis://:{MASK}@cache:6379")'
    assert mask_text("https://api.example.com:8443/v1") == "https://api.example.com:8443/v1"


def test_code_assignments_and_dict_literals():
    code = 'API_KEY = "abc123"\nheaders = {"Authorization": "Bearer abc123"}\nclient = OpenAI(api_key=secret["SecretString"])\n'
    assert mask_text(code) == \
        f'API_KEY = "{MASK}"\nheaders = {{"Authorization": "{MASK}"}}\nclient = OpenAI(api_key=secret["SecretString"])\n'


def test_nested_config():
    config = {"definition": {"States": {"Call": {"Parameters": {"Headers": {"x-api-key": "abc"}, "Url": "https://x.io"}}}}}
    assert mask(config)["definition"]["States"]["Call"]["Parameters"] == {"Headers": {"x-api-key": MASK}, "Url": "https://x.io"}


@pytest.mark.parametrize("text", [
    "anthropic.claude-3-5-sonnet-20240620-v1:0",
    "arn:aws:states:::sqs:sendMessage.waitForTaskToken",
    "s3://northwind-ml-models/churn-predictor/model.tar.gz",
    "683313688378.dkr.ecr.us-east-1.amazonaws.com/sagemaker-xgboost:1.7-1",
    '"max_tokens": 512,',
    "Key={'session_id': session_id}",
])
def test_ordinary_values_are_untouched(text):
    assert mask_text(text) == text


@mock_aws
def test_collectors_mask_env_and_code():
    seed_estate()
    lam = aws.session().client("lambda")
    lam.update_function_configuration(FunctionName="support-chatbot", Environment={"Variables": {
        "MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0", "OPENAI_API_KEY": "sk-proj-4fG7hK2mN9pQ1rS6tV8wX3yZ"}})
    resources, _ = collect_all(aws.session(), lambda name: {"handler.py": 'TOKEN = "ghp_live"\nbedrock.converse(modelId=M)'})
    chatbot = next(r for r in resources if r.name == "support-chatbot")
    assert chatbot.env == {"MODEL_ID": "anthropic.claude-3-haiku-20240307-v1:0", "OPENAI_API_KEY": MASK}
    assert chatbot.code == {"handler.py": f'TOKEN = "{MASK}"\nbedrock.converse(modelId=M)'}
    sales = next(r for r in resources if r.name == "sales-research-agent")
    assert sales.env["OPENAI_API_KEY_SECRET_ARN"] == MASK and sales.env["OPENAI_MODEL"] == "gpt-4o"
