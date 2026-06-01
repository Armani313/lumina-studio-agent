"""Deliver generated assets + package manifest to Cloud Storage."""
from __future__ import annotations

import uuid

from ..clients import gcs_bucket
from ..config import settings


def upload_bytes(data: bytes, blob_name: str, content_type: str) -> str:
    """Upload bytes to the assets bucket and return the gs:// URI."""
    blob = gcs_bucket().blob(blob_name)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{settings.gcs_bucket}/{blob_name}"


def public_https_url(gs_uri: str) -> str:
    """HTTPS form of a gs:// URI (reachable once the object/bucket grants read,
    otherwise serve via a signed URL at delivery time)."""
    path = gs_uri.replace("gs://", "", 1)
    return f"https://storage.googleapis.com/{path}"


def mime_for_uri(uri: str) -> str:
    """Best-effort image MIME type from a URI extension."""
    u = uri.lower()
    if u.endswith(".png"):
        return "image/png"
    if u.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def write_manifest(package_summary: str) -> dict:
    """Write the final package manifest (a JSON string) to GCS and return its location.

    Args:
        package_summary: JSON string describing the delivered assets (images, copy, QA).

    Returns:
        dict with gs_uri and https_url of the stored manifest.
    """
    blob_name = f"packages/{uuid.uuid4().hex}/manifest.json"
    gs_uri = upload_bytes(package_summary.encode("utf-8"), blob_name, "application/json")
    return {"gs_uri": gs_uri, "https_url": public_https_url(gs_uri)}
