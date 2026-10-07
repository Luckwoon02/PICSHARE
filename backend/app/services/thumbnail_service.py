import io
from typing import NamedTuple

from PIL import Image, ImageOps

# Face recognition doesn't need a 40-megapixel original. A copy of this size is sharp enough
# to find faces, always fits Rekognition's limits (JPEG/PNG only, 5 MB as bytes, 15 MB from S3)
# and decodes much faster than the original.
AI_MAX_EDGE = 3000
AI_MAX_BYTES = 4_500_000
_MIN_EDGE = 1000  # never shrink the Rekognition copy below this while chasing the size limit


class PreparedImage(NamedTuple):
    width: int  # of the original, as it is displayed (EXIF rotation applied)
    height: int
    ai_jpeg: bytes | None  # JPEG copy to send to Rekognition (None when it wasn't asked for)


class ThumbnailService:
    @staticmethod
    def generate_thumbnail(image_path: str, output_path: str, size=(512, 512)):
        with Image.open(image_path) as img:
            img.thumbnail(size)
            img.save(output_path, "JPEG", quality=85)

    @staticmethod
    def prepare(
        image_path: str, thumb_path: str, thumb_size=(512, 512), ai_copy: bool = True
    ) -> PreparedImage:
        """
        Read the photo once and produce everything the worker needs from it:
        its displayed size, a thumbnail written to thumb_path, and (unless ai_copy is False,
        for events without face scan) a JPEG copy for Rekognition.

        EXIF rotation is applied, so portrait photos stay upright in the thumbnail and
        Rekognition sees them the right way up. Raises if the file isn't a readable image.
        """
        with Image.open(image_path) as src:
            width, height = src.size
            if src.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # camera was held sideways
                width, height = height, width

            # For JPEGs the decoder can shrink the image while reading it, which saves a lot of
            # time and memory on big files. It never goes below the size requested here.
            scale = min(1.0, AI_MAX_EDGE / max(src.size))
            src.draft("RGB", (max(1, round(src.size[0] * scale)), max(1, round(src.size[1] * scale))))

            work = ImageOps.exif_transpose(src)
            if work.mode != "RGB":
                work = work.convert("RGB")

        if max(work.size) > AI_MAX_EDGE:
            work.thumbnail((AI_MAX_EDGE, AI_MAX_EDGE), Image.Resampling.LANCZOS)

        ai_jpeg = _encode_under_limit(work) if ai_copy else None

        thumb = work.copy()
        thumb.thumbnail(thumb_size, Image.Resampling.LANCZOS)
        thumb.save(thumb_path, "JPEG", quality=85)

        return PreparedImage(width, height, ai_jpeg)


def _encode_under_limit(image: Image.Image) -> bytes:
    """JPEG-encode, lowering quality and then size until the result fits Rekognition's byte limit."""
    quality = 85
    while True:
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=quality)
        data = buf.getvalue()
        edge = max(image.size)
        if len(data) <= AI_MAX_BYTES or edge <= _MIN_EDGE:
            return data
        if quality > 65:
            quality -= 10
        else:
            image.thumbnail((int(edge * 0.8), int(edge * 0.8)), Image.Resampling.LANCZOS)


thumbnail_service = ThumbnailService()
