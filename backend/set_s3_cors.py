"""
set_s3_cors.py — allow the browser to upload photos straight to the S3 bucket.

Usage (from backend/):
    python set_s3_cors.py https://your-site.com [http://localhost:3000 ...]

Replaces the bucket's CORS rules, so list every origin that needs upload access.
"""
import sys
import os

sys.path.append(os.getcwd())

import boto3
from app.core.config import get_settings


def main(origins: list[str]) -> None:
    s = get_settings()
    client = boto3.client(
        "s3",
        region_name=s.AWS_REGION,
        aws_access_key_id=s.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
    )
    client.put_bucket_cors(
        Bucket=s.S3_BUCKET_NAME,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedMethods": ["POST"],
                    "AllowedOrigins": origins,
                    "AllowedHeaders": ["*"],
                    "MaxAgeSeconds": 3000,
                }
            ]
        },
    )
    print(f"CORS set on {s.S3_BUCKET_NAME} for: {', '.join(origins)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
