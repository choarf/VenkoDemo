"""S3 access: dashboard config, latest IoT messages, report files."""
import json
import time

import boto3
from botocore.exceptions import ClientError

from . import settings

_s3 = boto3.client("s3")
_config_cache: dict = {"at": 0.0, "value": None}
CONFIG_TTL_S = 300


def get_json(key: str):
    try:
        body = _s3.get_object(Bucket=settings.BUCKET, Key=key)["Body"].read()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(body)


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    _s3.put_object(Bucket=settings.BUCKET, Key=key, Body=data, ContentType=content_type)


def presign(key: str, expires_s: int = 900) -> str:
    return _s3.generate_presigned_url(
        "get_object", Params={"Bucket": settings.BUCKET, "Key": key}, ExpiresIn=expires_s
    )


def list_keys(prefix: str) -> list[str]:
    keys = []
    for page in _s3.get_paginator("list_objects_v2").paginate(Bucket=settings.BUCKET, Prefix=prefix):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return keys


def get_config() -> dict:
    """Dashboard config published by tools/sync_config.py (cached per container)."""
    if _config_cache["value"] is None or time.time() - _config_cache["at"] > CONFIG_TTL_S:
        cfg = get_json(settings.CONFIG_KEY)
        if cfg is None:
            raise LookupError(f"s3://{settings.BUCKET}/{settings.CONFIG_KEY} missing - run tools/sync_config.py")
        _config_cache.update(at=time.time(), value=cfg)
    return _config_cache["value"]
