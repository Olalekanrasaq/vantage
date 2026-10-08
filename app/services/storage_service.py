import io
import uuid

import boto3
from botocore.config import Config
from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_SIDE = 1600
MAX_PIXELS = 50_000_000     # guards against decompression bombs
PREFIX = "visits/"

_client = None


class ImageError(Exception):
    """Raised when an uploaded file is not a usable image."""


def _s3():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            region_name=current_app.config["AWS_REGION"],
            config=Config(signature_version="s3v4"),
        )
    return _client


def _shrink(file_storage) -> bytes:
    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        if img.width * img.height > MAX_PIXELS:
            raise ImageError("Image dimensions are too large.")
        img = ImageOps.exif_transpose(img)      # keep phone orientation
        img = img.convert("RGB")                # drops alpha, strips metadata
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80, optimize=True)
        return buf.getvalue()
    except (UnidentifiedImageError, OSError):
        raise ImageError("That file is not a valid image.")


def upload_visit_photo(file_storage, manager_id, visit_date) -> str:
    """Resize, upload to S3, return the object key to store in the DB."""
    body = _shrink(file_storage)
    key = f"{PREFIX}{manager_id}/{visit_date}/{uuid.uuid4().hex}.jpg"
    _s3().put_object(
        Bucket=current_app.config["S3_BUCKET"],
        Key=key,
        Body=body,
        ContentType="image/jpeg",
        ServerSideEncryption="AES256",
    )
    return key


def delete_photo(key):
    if key and key.startswith(PREFIX):
        try:
            _s3().delete_object(Bucket=current_app.config["S3_BUCKET"], Key=key)
        except Exception:
            current_app.logger.exception("Could not delete %s", key)


def photo_url(key, expires=600):
    """Template helper: signed S3 link (10 min). Old local files still resolve."""
    if not key:
        return None
    if not key.startswith(PREFIX):      # legacy rows saved before S3
        return url_for("static", filename=f"uploads/visits/{key}")
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": current_app.config["S3_BUCKET"], "Key": key},
        ExpiresIn=expires,
    )