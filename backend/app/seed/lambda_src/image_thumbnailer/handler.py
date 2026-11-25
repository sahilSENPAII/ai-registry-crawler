import io
import os

import boto3
from PIL import Image

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
SIZES = [(128, 128), (512, 512)]

s3 = boto3.client("s3")


def lambda_handler(event, context):
    for record in event["Records"]:
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        original = Image.open(io.BytesIO(s3.get_object(Bucket=bucket, Key=key)["Body"].read()))
        for width, height in SIZES:
            image = original.copy()
            image.thumbnail((width, height))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=85)
            s3.put_object(Bucket=OUTPUT_BUCKET, Key=f"{width}x{height}/{key}", Body=buffer.getvalue(), ContentType="image/jpeg")
