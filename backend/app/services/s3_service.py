import asyncio
import boto3
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
        )
        self.bucket = s.S3_BUCKET_NAME

    # ------------------------------------------------------------------
    # Upload helpers
    # ------------------------------------------------------------------

    async def upload_file(
        self, local_path: str, s3_key: str, content_type: str = "image/jpeg"
    ) -> str:
        """Upload a local file to S3. Returns the s3_key on success."""
        def _upload():
            self._client.upload_file(
                local_path,
                self.bucket,
                s3_key,
                ExtraArgs={"ContentType": content_type},
            )

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

    # ------------------------------------------------------------------
    # Presigned URL
    # ------------------------------------------------------------------

    async def get_presigned_url(
        self, s3_key: str, expiry_seconds: int = 3600
    ) -> str:
        """Generate a presigned GET URL valid for expiry_seconds."""
        def _generate():
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": s3_key},
                ExpiresIn=expiry_seconds,
            )

        return await asyncio.to_thread(_generate)

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
