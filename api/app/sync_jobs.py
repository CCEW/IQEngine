import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from .azure_client import AzureBlobClient
from .database import db
from .datasources import get
from .metadata import validate_metadata
from .models import SyncJobResponse
from helpers.cipher import decrypt
from helpers.samples import get_bytes_per_iq_sample

ACTIVE_STATUSES = {"queued", "running"}
DEFAULT_RETENTION_HOURS = 24 * 7
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 0.2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | None) -> datetime | None:
    """MongoDB returns naive UTC datetimes; make them comparable to _now()."""
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _job_response(document: dict[str, Any]) -> SyncJobResponse:
    return SyncJobResponse(**{key: value for key, value in document.items() if key != "_id"})


async def claim_sync_job(account: str, container: str) -> tuple[str, bool]:
    jobs = db().sync_jobs
    await jobs.create_index(
        [("account", 1), ("container", 1)],
        unique=True,
        partialFilterExpression={"status": {"$in": list(ACTIVE_STATUSES)}},
    )
    existing = await jobs.find_one(
        {"account": account, "container": container, "status": {"$in": list(ACTIVE_STATUSES)}},
        sort=[("created_at", -1)],
    )
    if existing:
        return existing["job_id"], False

    job_id = str(uuid.uuid4())
    now = _now()
    try:
        await jobs.insert_one(
            {
            "job_id": job_id,
            "account": account,
            "container": container,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "duration_seconds": None,
            "object_counts": {"metadata": 0, "data": 0, "indexed": 0, "invalid": 0, "incomplete": 0, "missing": 0, "deleted": 0, "changed": 0},
            "error": None,
            }
        )
    except DuplicateKeyError:
        existing = await jobs.find_one(
            {"account": account, "container": container, "status": {"$in": list(ACTIVE_STATUSES)}},
            sort=[("created_at", -1)],
        )
        if existing:
            return existing["job_id"], False
        raise
    return job_id, True


async def get_job(job_id: str) -> SyncJobResponse | None:
    document = await db().sync_jobs.find_one({"job_id": job_id})
    return _job_response(document) if document else None


async def _retry(operation, *args, **kwargs):
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except (ClientError, OSError, TimeoutError) as error:
            last_error = error
            if attempt + 1 < MAX_RETRIES:
                await asyncio.sleep(RETRY_DELAY_SECONDS * (2**attempt))
    raise last_error


async def _inventory(datasource) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], AzureBlobClient]:
    client = AzureBlobClient(datasource.account, datasource.container, datasource.awsAccessKeyId, datasource.s3EndpointUrl, datasource.s3VerifySsl)
    if datasource.sasToken:
        client.set_sas_token(decrypt(datasource.sasToken.get_secret_value()))
    if datasource.awsSecretAccessKey:
        client.set_aws_secret_access_key(decrypt(datasource.awsSecretAccessKey.get_secret_value()))

    metadata_objects: dict[str, dict[str, Any]] = {}
    data_objects: dict[str, dict[str, Any]] = {}
    if datasource.account == "local":
        for root, _, files in os.walk(client.base_filepath):
            for name in files:
                path = os.path.relpath(os.path.join(root, name), client.base_filepath).replace(os.sep, "/")
                target = metadata_objects if name.endswith(".sigmf-meta") else data_objects if name.endswith(".sigmf-data") else None
                if target is not None:
                    target[path] = {"fingerprint": str(os.path.getmtime(os.path.join(root, name)),), "size": os.path.getsize(os.path.join(root, name))}
    elif datasource.awsAccessKeyId:
        def list_objects():
            s3 = boto3.client("s3", aws_access_key_id=datasource.awsAccessKeyId, aws_secret_access_key=client.awsSecretAccessKey.get_secret_value(), region_name=datasource.account, endpoint_url=datasource.s3EndpointUrl, verify=datasource.s3VerifySsl, config=Config(s3={"addressing_style": "path"}) if datasource.s3EndpointUrl else None)
            paginator = s3.get_paginator("list_objects_v2")
            result = []
            for page in paginator.paginate(Bucket=datasource.container, Prefix=datasource.s3Prefix or ""):
                result.extend(page.get("Contents", []))
            return result
        for obj in await _retry(list_objects):
            target = metadata_objects if obj["Key"].endswith(".sigmf-meta") else data_objects if obj["Key"].endswith(".sigmf-data") else None
            if target is not None:
                target[obj["Key"]] = {"fingerprint": obj.get("ETag", "").strip('"') or obj.get("LastModified"), "size": obj.get("Size", 0)}
    else:
        async for blob in client.get_container_client().list_blobs(include=["metadata"]):
            target = metadata_objects if blob.name.endswith(".sigmf-meta") else data_objects if blob.name.endswith(".sigmf-data") else None
            if target is not None:
                target[blob.name] = {"fingerprint": getattr(blob, "etag", None) or getattr(blob, "last_modified", None), "size": blob.size}
    return metadata_objects, data_objects, client


async def reconcile(account: str, container: str, job_id: str) -> None:
    jobs = db().sync_jobs
    started = _now()
    await jobs.update_one({"job_id": job_id}, {"$set": {"status": "running", "started_at": started}})
    client = None
    counts = {"metadata": 0, "data": 0, "indexed": 0, "invalid": 0, "incomplete": 0, "missing": 0, "deleted": 0, "changed": 0}
    errors: list[dict[str, str]] = []
    try:
        datasource = await get(account, container)
        if datasource is None:
            raise HTTPException(status_code=404, detail="Datasource not found")
        metadata_objects, data_objects, client = await _inventory(datasource)
        counts["metadata"] = len(metadata_objects)
        counts["data"] = len(data_objects)
        seen_paths = set()
        metadata_collection = db().metadata
        for meta_key, source in metadata_objects.items():
            path = meta_key[: -len(".sigmf-meta")]
            data_key = path + ".sigmf-data"
            if data_key not in data_objects:
                counts["incomplete"] += 1
                errors.append({"path": path, "code": "missing_data", "detail": f"Missing matching object {data_key}"})
                continue
            try:
                raw = await client.get_blob_content(meta_key)
                metadata = validate_metadata(json.loads(raw))
                metadata["global"]["traceability:origin"] = {"type": "api", "account": account, "container": container, "file_path": path}
                metadata["global"]["traceability:revision"] = 0
                metadata["global"]["traceability:sample_length"] = data_objects[data_key]["size"] / get_bytes_per_iq_sample(metadata["global"]["core:datatype"])
            except Exception as error:
                counts["invalid"] += 1
                errors.append({"path": path, "code": "invalid_metadata", "detail": str(error)})
                continue
            existing = await metadata_collection.find_one({"global.traceability:origin.account": account, "global.traceability:origin.container": container, "global.traceability:origin.file_path": path})
            changed = existing and (existing.get("catalog_fingerprint") != source["fingerprint"] or existing.get("data_fingerprint") != data_objects[data_key]["fingerprint"])
            metadata.update({"catalog_status": "active", "catalog_fingerprint": source["fingerprint"], "data_fingerprint": data_objects[data_key]["fingerprint"], "last_seen_at": _now(), "missing_since": None})
            await metadata_collection.replace_one({"global.traceability:origin.account": account, "global.traceability:origin.container": container, "global.traceability:origin.file_path": path}, metadata, upsert=True)
            seen_paths.add(path)
            counts["indexed"] += 1
            if changed:
                counts["changed"] += 1
        cursor = metadata_collection.find({"global.traceability:origin.account": account, "global.traceability:origin.container": container, "catalog_status": {"$in": ["active", "missing"]}})
        retention = timedelta(hours=float(os.getenv("IQENGINE_SYNC_RETENTION_HOURS", DEFAULT_RETENTION_HOURS)))
        async for record in cursor:
            path = record["global"]["traceability:origin"]["file_path"]
            if path in seen_paths:
                continue
            missing_since = as_utc(record.get("missing_since")) or _now()
            status = "deleted" if _now() - missing_since >= retention else "missing"
            await metadata_collection.update_one({"_id": record["_id"]}, {"$set": {"catalog_status": status, "missing_since": missing_since, "last_seen_at": record.get("last_seen_at")}})
            counts[status] += 1
        completed = _now()
        await jobs.update_one({"job_id": job_id}, {"$set": {"status": "completed", "completed_at": completed, "duration_seconds": (completed - started).total_seconds(), "object_counts": counts, "error": errors or None}})
    except Exception as error:
        completed = _now()
        await jobs.update_one({"job_id": job_id}, {"$set": {"status": "failed", "completed_at": completed, "duration_seconds": (completed - started).total_seconds(), "object_counts": counts, "error": [{"code": "sync_failed", "detail": str(error)}]}})
    finally:
        if client is not None:
            await client.close_blob_clients()


async def start_sync(account: str, container: str) -> tuple[str, bool]:
    return await claim_sync_job(account, container)
