import io
import urllib.request
import zipfile
from contextlib import contextmanager

import boto3
from moto import mock_aws
from moto.awslambda.models import lambda_backends

from app.config import config


def session() -> boto3.Session:
    return boto3.Session(region_name=config.aws_regions[0])


@contextmanager
def simulated_aws():
    # imported here: the seed code is only needed for the simulation, and it imports this module
    from app.seed.agent_logs import seed_agent_logs
    from app.seed.estate import seed_estate

    # Bedrock runtime passes through to real AWS, so the crawler's LLM works on the simulated estate
    with mock_aws(config={"core": {"passthrough": {"services": ["bedrockruntime"]}}}):
        seed_estate()
        seed_agent_logs()
        yield


def lambda_code_fetcher(mode: str = config.discovery_mode):
    def fetch(function_name: str) -> dict[str, str]:
        s = session()
        if mode == "mock":
            account = s.client("sts").get_caller_identity()["Account"]
            code = lambda_backends[account][s.region_name].get_function(function_name).code_bytes
        else:
            location = s.client("lambda").get_function(FunctionName=function_name)["Code"]["Location"]
            with urllib.request.urlopen(location) as resp:
                code = resp.read()
        with zipfile.ZipFile(io.BytesIO(code)) as zf:
            return {name: zf.read(name).decode(errors="replace") for name in zf.namelist() if not name.endswith("/")}

    return fetch
