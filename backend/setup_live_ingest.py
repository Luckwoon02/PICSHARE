"""
setup_live_ingest.py — one-time AWS setup for live mode.

Creates an SQS queue (plus a dead-letter queue for messages that keep failing) and tells the S3 bucket to
send a message to it whenever a file lands under live/ . The backend reads that queue (INGEST_QUEUE_URL)
and turns each file into a photo.

Usage (from backend/):
    python setup_live_ingest.py --dry-run             # show what would happen, change nothing
    python setup_live_ingest.py                       # do it
    python setup_live_ingest.py --profile my-admin    # use an AWS CLI profile instead of the keys in backend/.env

The AWS user in backend/.env normally cannot create queues or edit bucket notifications; run this with a
profile that can (sqs:CreateQueue, sqs:SetQueueAttributes, s3:GetBucketNotification, s3:PutBucketNotification).

Safe to run again: it reuses the queues and updates its own bucket rule. It does NOT replace other
notification rules on the bucket; if one overlaps live/, it stops and says so.
"""
import argparse
import json
import sys
import os

sys.path.append(os.getcwd())

import boto3
from botocore.exceptions import ClientError
from app.core.config import get_settings

LIVE_PREFIX = "live/"
RULE_ID = "picshare-live-ingest"


def _overlaps(prefix: str) -> bool:
    """S3 refuses two notification rules whose prefixes overlap for the same events."""
    return prefix.startswith(LIVE_PREFIX) or LIVE_PREFIX.startswith(prefix)


def _rule_prefix(rule: dict) -> str:
    for f in rule.get("Filter", {}).get("Key", {}).get("FilterRules", []):
        if f["Name"].lower() == "prefix":
            return f["Value"]
    return ""


def _find_conflicts(config: dict) -> list[str]:
    conflicts = []
    for kind in ("QueueConfigurations", "TopicConfigurations", "LambdaFunctionConfigurations"):
        for rule in config.get(kind, []):
            if rule.get("Id") == RULE_ID:
                continue
            created = any(e.startswith("s3:ObjectCreated") for e in rule.get("Events", []))
            if created and _overlaps(_rule_prefix(rule)):
                conflicts.append(f"{kind[:-14]} rule '{rule.get('Id', '(no id)')}' with prefix '{_rule_prefix(rule)}'")
    return conflicts


def _queue(sqs, name: str, attributes: dict, dry_run: bool) -> tuple[str | None, str | None]:
    """Create the queue (or update the existing one). Returns (url, arn); both None for a dry run of a new queue."""
    try:
        url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
        print(f"  queue {name}: exists")
        if not dry_run:
            sqs.set_queue_attributes(QueueUrl=url, Attributes=attributes)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("AWS.SimpleQueueService.NonExistentQueue", "QueueDoesNotExist"):
            raise
        print(f"  queue {name}: {'would be created' if dry_run else 'creating'}")
        if dry_run:
            return None, None
        url = sqs.create_queue(QueueName=name, Attributes=attributes)["QueueUrl"]
    arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    return url, arn


def main(queue_name: str, profile: str | None, dry_run: bool) -> None:
    s = get_settings()
    if not s.S3_BUCKET_NAME:
        sys.exit("S3_BUCKET_NAME is not set in backend/.env")

    if profile:
        session = boto3.Session(profile_name=profile, region_name=s.AWS_REGION)
    else:
        session = boto3.Session(
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
            region_name=s.AWS_REGION,
        )
    sts, sqs, s3 = session.client("sts"), session.client("sqs"), session.client("s3")

    account = sts.get_caller_identity()["Account"]
    bucket_arn = f"arn:aws:s3:::{s.S3_BUCKET_NAME}"
    print(f"{'DRY RUN: ' if dry_run else ''}bucket {s.S3_BUCKET_NAME} ({s.AWS_REGION}), account {account}")

    existing = s3.get_bucket_notification_configuration(Bucket=s.S3_BUCKET_NAME)
    existing.pop("ResponseMetadata", None)
    conflicts = _find_conflicts(existing)
    if conflicts:
        sys.exit(
            "The bucket already has notification rule(s) that overlap live/ and would be rejected by S3:\n  "
            + "\n  ".join(conflicts)
            + "\nNothing was changed. Narrow or remove those rules first."
        )

    print("queues:")
    _, dlq_arn = _queue(sqs, f"{queue_name}-dlq", {"MessageRetentionPeriod": "1209600"}, dry_run)
    attributes = {
        "VisibilityTimeout": "120",           # a photo batch is registered in well under this
        "ReceiveMessageWaitTimeSeconds": "20",
        "MessageRetentionPeriod": "345600",   # 4 days
    }
    if dlq_arn:
        attributes["RedrivePolicy"] = json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "5"})
    queue_url, queue_arn = _queue(sqs, queue_name, attributes, dry_run)

    others = sum(1 for v in existing.values() if isinstance(v, list) for r in v if r.get("Id") != RULE_ID)
    print(f"bucket notification: {'would add' if dry_run else 'adding'} rule '{RULE_ID}' "
          f"(s3:ObjectCreated:* under {LIVE_PREFIX}); {others} other rule(s) left as they are")
    if dry_run:
        print("\nNothing was changed. Run again without --dry-run to apply.")
        return

    # Only this bucket, from this account, may put messages on the queue
    sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={"Policy": json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "AllowS3Notifications",
            "Effect": "Allow",
            "Principal": {"Service": "s3.amazonaws.com"},
            "Action": "sqs:SendMessage",
            "Resource": queue_arn,
            "Condition": {
                "ArnEquals": {"aws:SourceArn": bucket_arn},
                "StringEquals": {"aws:SourceAccount": account},
            },
        }],
    })})

    # put_bucket_notification_configuration REPLACES everything, so send back the existing rules too
    merged = {k: list(v) for k, v in existing.items() if isinstance(v, list)}
    if "EventBridgeConfiguration" in existing:
        merged["EventBridgeConfiguration"] = existing["EventBridgeConfiguration"]
    merged["QueueConfigurations"] = [r for r in merged.get("QueueConfigurations", []) if r.get("Id") != RULE_ID] + [{
        "Id": RULE_ID,
        "QueueArn": queue_arn,
        "Events": ["s3:ObjectCreated:*"],
        "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": LIVE_PREFIX}]}},
    }]
    s3.put_bucket_notification_configuration(Bucket=s.S3_BUCKET_NAME, NotificationConfiguration=merged)

    print("\nDone. Next:")
    print(f"  1. Add this to backend/.env, then restart the backend:\n       INGEST_QUEUE_URL={queue_url}")
    print("  2. Give the AWS user in backend/.env permission to read the queue:")
    print(json.dumps({
        "Effect": "Allow",
        "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
        "Resource": queue_arn,
    }, indent=2))
    print("  Only set INGEST_QUEUE_URL where the backend really runs (not on teammates' laptops):\n"
          "  two backends reading one queue would split the messages between them.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the live-mode ingest queue and the S3 notification.")
    parser.add_argument("--queue-name", default="picshare-live-ingest")
    parser.add_argument("--profile", help="AWS CLI profile to use instead of the keys in backend/.env")
    parser.add_argument("--dry-run", action="store_true", help="show what would be done and change nothing")
    args = parser.parse_args()
    main(args.queue_name, args.profile, args.dry_run)
