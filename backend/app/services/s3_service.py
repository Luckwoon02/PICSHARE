import asyncio
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.core.config import get_settings


class S3Service:
    def __init__(self):
        s = get_settings()
        self._client = boto3.client(
            "s3",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
            # Regional endpoint so browser uploads aren't redirected (a redirect breaks CORS POSTs)
            endpoint_url=f"https://s3.{s.AWS_REGION}.amazonaws.com",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
                max_pool_connections=32,
                retries={"max_attempts": 5, "mode": "adaptive"},
            ),
        )
        self.bucket = s.S3_BUCKET_NAME
        # (key, expiry) -> (url, reuse_until). See get_presigned_url.
        self._url_cache: dict[tuple[str, int], tuple[str, float]] = {}

    # ------------------------------------------------------------------
    # Upload helpers
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        local_path: str,
        s3_key: str,
        content_type: str = "image/jpeg",
        cache_control: str | None = None,
    ) -> str:
        """Upload a local file to S3. Returns the s3_key on success."""
        extra = {"ContentType": content_type}
        if cache_control:
            extra["CacheControl"] = cache_control

        def _upload():
            self._client.upload_file(local_path, self.bucket, s3_key, ExtraArgs=extra)

        await asyncio.to_thread(_upload)
        return s3_key

    async def upload_bytes(
        self, data: bytes, s3_key: str, content_type: str = "image/jpeg"
    ) -> str:
        """Upload raw bytes to S3. Returns the s3_key on success."""
        def _upload():
            self._client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=data,
                ContentType=content_type,
            )

        await asyncio.to_thread(_upload)
        return s3_key

    async def create_presigned_upload(
        self,
        s3_key: str,
        content_type: str,
        max_bytes: int,
        expiry_seconds: int = 3600,
    ) -> dict:
        """
        Presigned POST so the browser can upload straight to S3.
        Returns {"url": ..., "fields": {...}}. S3 itself rejects the upload if the
        file is larger than max_bytes or the Content-Type differs.
        """
        def _generate():
            return self._client.generate_presigned_post(
                Bucket=self.bucket,
                Key=s3_key,
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 1, max_bytes],
                ],
                ExpiresIn=expiry_seconds,
            )

        return await asyncio.to_thread(_generate)

    async def get_object_size(self, s3_key: str) -> int | None:
        """Size in bytes of the object, or None if it doesn't exist."""
        def _head():
            try:
                return self._client.head_object(Bucket=self.bucket, Key=s3_key)["ContentLength"]
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return None
                raise

        return await asyncio.to_thread(_head)

    async def object_exists(self, s3_key: str) -> bool:
        """True if the key exists in the bucket."""
        def _head():
            try:
                self._client.head_object(Bucket=self.bucket, Key=s3_key)
                return True
            except ClientError as e:
                if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                    return False
                raise

        return await asyncio.to_thread(_head)

    async def download_file(self, s3_key: str, local_path: str) -> None:
        """Download an S3 object to a local path."""
        await asyncio.to_thread(
            self._client.download_file, self.bucket, s3_key, local_path
        )

    # ------------------------------------------------------------------
    # Presigned URL
    # ------------------------------------------------------------------

    async def get_presigned_url(
        self, s3_key: str, expiry_seconds: int = 3600
    ) -> str:
        """
        Presigned GET URL valid for at least a quarter of expiry_seconds.

        The same URL is handed out repeatedly until then. A freshly signed URL differs
        on every call, so the browser would treat each dashboard refresh as new images
        and re-download every thumbnail.
        """
        cache_key = (s3_key, expiry_seconds)
        now = time.monotonic()
        hit = self._url_cache.get(cache_key)
        if hit and hit[1] > now:
            return hit[0]

        def _generate():
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": s3_key},
                ExpiresIn=expiry_seconds,
            )

        url = await asyncio.to_thread(_generate)
        if len(self._url_cache) > 20000:  # keep memory bounded
            self._url_cache = {k: v for k, v in self._url_cache.items() if v[1] > now}
        self._url_cache[cache_key] = (url, now + expiry_seconds * 0.75)
        return url

    async def get_presigned_download_url(
        self,
        s3_key: str,
        filename: str,
        expiry_seconds: int = 3600,
    ) -> str:
        """
        Presigned GET URL that forces browser download via
        Content-Disposition: attachment.
        """
        def _generate():
            return self._client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.bucket,
                    "Key": s3_key,
                    "ResponseContentDisposition": f'attachment; filename="{filename}"',
                },
                ExpiresIn=expiry_seconds,
            )

        return await asyncio.to_thread(_generate)

    # ------------------------------------------------------------------
    # Delete helpers
    # ------------------------------------------------------------------

    async def delete_object(self, s3_key: str) -> None:
        """Delete a single S3 object. Silent if it does not exist."""
        def _delete():
            self._client.delete_object(Bucket=self.bucket, Key=s3_key)

        await asyncio.to_thread(_delete)

    async def delete_prefix(self, prefix: str) -> int:
        """
        Delete all S3 objects whose key starts with prefix.
        Uses paginated list + batch delete (up to 1 000 keys per request).
        Returns the number of objects deleted.
        """
        deleted_count = 0

        def _list_and_delete():
            nonlocal deleted_count
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                contents = page.get("Contents", [])
                if not contents:
                    continue
                objects = [{"Key": obj["Key"]} for obj in contents]
                self._client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
                deleted_count += len(objects)

        await asyncio.to_thread(_list_and_delete)
        return deleted_count

    # ------------------------------------------------------------------
    # Storage info
    # ------------------------------------------------------------------

    async def get_prefix_size(self, prefix: str) -> tuple[int, int]:
        """
        Returns (total_bytes, object_count) for all objects under prefix.
        """
        total_bytes = 0
        total_count = 0

        def _sum():
            nonlocal total_bytes, total_count
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    total_bytes += obj["Size"]
                    total_count += 1

        await asyncio.to_thread(_sum)
        return total_bytes, total_count


# Module-level singleton
s3_service = S3Service()
