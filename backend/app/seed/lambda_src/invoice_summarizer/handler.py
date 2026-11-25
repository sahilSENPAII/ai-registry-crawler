import json
import os

import boto3

MODEL_ID = os.environ["MODEL_ID"]
OUTPUT_TABLE = os.environ["SUMMARY_TABLE"]

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")
summaries = boto3.resource("dynamodb").Table(OUTPUT_TABLE)


def lambda_handler(event, context):
    record = event["Records"][0]["s3"]
    bucket, key = record["bucket"]["name"], record["object"]["key"]
    pdf = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{
            "role": "user",
            "content": [
                {"document": {"format": "pdf", "name": "invoice", "source": {"bytes": pdf}}},
                {"text": "Summarise this supplier invoice: supplier, invoice number, due date, total, and any unusual line items."},
            ],
        }],
        inferenceConfig={"maxTokens": 400, "temperature": 0},
    )
    summary = response["output"]["message"]["content"][0]["text"]
    summaries.put_item(Item={"invoice_key": key, "summary": summary})
    return {"invoice": key, "summary": summary}
