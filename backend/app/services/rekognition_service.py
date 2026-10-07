import asyncio
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from app.core.config import get_settings

# Errors where the image itself is the problem: sending it again will never work.
PERMANENT_ERRORS = {
    "ImageTooLargeException": "image is too large for face scan",
    "InvalidImageFormatException": "image format is not supported (JPEG or PNG only)",
    "InvalidParameterException": "image could not be scanned (too small or not a valid image)",
    "InvalidS3ObjectException": "image could not be read from storage",
    "AccessDeniedException": "AWS permission for face scan is missing",
    "ResourceNotFoundException": "this event's face collection does not exist",
}
# Amazon is telling us to slow down. Not the photo's fault, so it must not use up the photo's attempts.
THROTTLE_ERRORS = {"ProvisionedThroughputExceededException", "ThrottlingException", "Throttling"}


def error_code(exc: Exception) -> str:
    return exc.response["Error"]["Code"] if isinstance(exc, ClientError) else ""


def classify_error(exc: Exception) -> str:
    """'permanent' (never retry), 'throttled' (wait for Amazon) or 'transient' (try again later)."""
    code = error_code(exc)
    if code in PERMANENT_ERRORS:
        return "permanent"
    if code in THROTTLE_ERRORS:
        return "throttled"
    return "transient"


def describe_error(exc: Exception) -> str:
    code = error_code(exc)
    if code in PERMANENT_ERRORS:
        return f"Face scan skipped: {PERMANENT_ERRORS[code]}"
    return f"Face scan failed: {type(exc).__name__}: {exc}"


class RekognitionService:
    def __init__(self):
        s = get_settings()
        self._client = boto3.client(
            "rekognition",
            region_name=s.AWS_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
            # One quick retry for a network blip. Anything longer is the queue's job; stacking
            # retries here as well multiplies the calls made against a throttled account.
            config=Config(max_pool_connections=32, retries={"total_max_attempts": 2, "mode": "standard"}),
        )
        self.bucket = s.S3_BUCKET_NAME
        self.threshold = s.REKOGNITION_FACE_MATCH_THRESHOLD

        # Spacing between IndexFaces calls, so we stay under the account's requests-per-second quota.
        self._interval = 1.0 / max(s.REKOGNITION_MAX_TPS, 0.1)
        self._next_slot = 0.0
        self._slot_lock = asyncio.Lock()
        self._pause_until = 0.0

    # ------------------------------------------------------------------
    # Pacing
    # ------------------------------------------------------------------

    def pause(self, seconds: float) -> None:
        """Hold back every IndexFaces call for a while (Amazon said it was being asked too much)."""
        self._pause_until = max(self._pause_until, time.monotonic() + seconds)

    async def _wait_turn(self) -> None:
        while True:
            delay = self._pause_until - time.monotonic()
            if delay <= 0:
                break
            await asyncio.sleep(delay)
        async with self._slot_lock:
            now = time.monotonic()
            slot = max(self._next_slot, now)
            self._next_slot = slot + self._interval
        if slot > now:
            await asyncio.sleep(slot - now)

    # ------------------------------------------------------------------
    # Collection lifecycle
    # ------------------------------------------------------------------

    async def create_event_collection(self, event_id: str) -> bool:
        """
        Create a Rekognition collection whose ID is the event_id.
        Safe to call if the collection already exists — returns True when
        the collection is ready, False on unexpected error.
        """
        def _create():
            self._client.create_collection(CollectionId=event_id)

        try:
            await asyncio.to_thread(_create)
            print(f"[rekognition] Created collection: {event_id}")
            return True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceAlreadyExistsException":
                print(f"[rekognition] Collection already exists: {event_id}")
                return True
            print(f"[rekognition] create_collection error ({code}): {e}")
            return False

    async def delete_event_collection(self, event_id: str) -> bool:
        """
        Delete the Rekognition collection for the event.
        Safe to call if the collection does not exist.
        Returns True when done, False on unexpected error.
        """
        def _delete():
            self._client.delete_collection(CollectionId=event_id)

        try:
            await asyncio.to_thread(_delete)
            print(f"[rekognition] Deleted collection: {event_id}")
            return True
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ResourceNotFoundException":
                print(f"[rekognition] Collection not found (already gone): {event_id}")
                return True
            print(f"[rekognition] delete_collection error ({code}): {e}")
            return False

    # ------------------------------------------------------------------
    # Face indexing
    # ------------------------------------------------------------------

    async def index_photo_faces(
        self, s3_object_key: str, photo_id: str, event_id: str
    ) -> list[dict]:
        """
        Index all faces found in the S3 image against the event's collection.

        Returns a list of dicts:
            {
                "rekognition_face_id": str,   # UUID assigned by Rekognition
                "confidence": float,           # detection confidence (0-100)
                "bbox": {                      # relative bounding box
                    "Width": float, "Height": float,
                    "Left": float,  "Top": float
                }
            }

        ExternalImageId is set to photo_id so SearchFacesByImage results can
        be mapped back to a photo without an extra DB lookup.

        Amazon reads the image from S3, so it must be a JPEG/PNG under 15 MB. The photo
        worker uses index_image_bytes instead, which has no such surprises.
        """
        return await self._index_faces(
            {"S3Object": {"Bucket": self.bucket, "Name": s3_object_key}}, photo_id, event_id
        )

    async def index_image_bytes(
        self, image_bytes: bytes, photo_id: str, event_id: str
    ) -> list[dict]:
        """Like index_photo_faces, but sends the image itself (JPEG/PNG, at most 5 MB)."""
        return await self._index_faces({"Bytes": image_bytes}, photo_id, event_id)

    async def _index_faces(self, image: dict, photo_id: str, event_id: str) -> list[dict]:
        def _index():
            return self._client.index_faces(
                CollectionId=event_id,
                Image=image,
                ExternalImageId=photo_id,
                DetectionAttributes=["DEFAULT"],
                QualityFilter="AUTO",
            )

        await self._wait_turn()
        try:
            response = await asyncio.to_thread(_index)
        except ClientError as e:
            # Never report "no faces" for a call that actually failed: raise so the
            # worker can decide what to do (retry later, or give up on this photo).
            print(f"[rekognition] index_faces error for {photo_id} ({error_code(e)}): {e}")
            raise

        results = []
        for record in response.get("FaceRecords", []):
            face = record["Face"]
            results.append(
                {
                    "rekognition_face_id": face["FaceId"],
                    "confidence": face.get("Confidence", 0.0),
                    "bbox": face.get("BoundingBox", {}),
                }
            )

        unindexed = response.get("UnindexedFaces", [])
        if unindexed:
            print(
                f"[rekognition] {len(unindexed)} face(s) not indexed for photo "
                f"{photo_id} (quality filter / small face)"
            )

        return results

    # ------------------------------------------------------------------
    # Guest selfie search
    # ------------------------------------------------------------------

    async def search_guest_selfie(
        self, image_bytes: bytes, event_id: str
    ) -> list[dict]:
        """
        Search for faces matching the guest selfie within the event collection.

        Returns a list of dicts (deduplicated by photo_id, highest similarity kept):
            {
                "photo_id":              str,    # ExternalImageId set during IndexFaces
                "similarity":            float,  # 0-100
                "rekognition_face_id":   str,
            }

        Results are sorted by similarity descending.
        """
        def _search():
            return self._client.search_faces_by_image(
                CollectionId=event_id,
                Image={"Bytes": image_bytes},
                FaceMatchThreshold=self.threshold,
                MaxFaces=4096,
            )

        try:
            response = await asyncio.to_thread(_search)
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("InvalidParameterException", "InvalidImageException"):
                # No face detected in the selfie image
                print(f"[rekognition] search_faces_by_image: no face in selfie ({code})")
                return []
            if code == "ResourceNotFoundException":
                print(f"[rekognition] Collection not found for event {event_id}")
                return []
            print(f"[rekognition] search_faces_by_image error ({code}): {e}")
            return []

        # Deduplicate by photo_id — keep the highest similarity match per photo
        best: dict[str, dict] = {}
        for match in response.get("FaceMatches", []):
            face = match["Face"]
            photo_id = face.get("ExternalImageId", "")
            similarity = match.get("Similarity", 0.0)
            face_id = face.get("FaceId", "")

            if photo_id not in best or similarity > best[photo_id]["similarity"]:
                best[photo_id] = {
                    "photo_id": photo_id,
                    "similarity": similarity,
                    "rekognition_face_id": face_id,
                }

        return sorted(best.values(), key=lambda x: x["similarity"], reverse=True)


# Module-level singleton
rekognition_service = RekognitionService()
