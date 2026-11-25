import os

import boto3

KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]

agent_runtime = boto3.client("bedrock-agent-runtime")


def lambda_handler(event, context):
    question = event["question"]
    response = agent_runtime.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KNOWLEDGE_BASE_ID,
                "modelArn": MODEL_ARN,
                "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 6}},
            },
        },
    )
    citations = [
        ref["location"]["s3Location"]["uri"]
        for citation in response["citations"]
        for ref in citation["retrievedReferences"]
    ]
    return {"answer": response["output"]["text"], "sources": sorted(set(citations))}
