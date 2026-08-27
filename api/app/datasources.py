import asyncio
import json
import os
import time
from typing import Optional

import boto3
import numpy as np
from bson import encode
from bson.raw_bson import RawBSONDocument
from botocore.config import Config
from fastapi import Depends
from helpers.cipher import decrypt, encrypt
from helpers.datasource_access import check_access
from helpers.samples import get_bytes_per_iq_sample
from pydantic import SecretStr
from pymongo.operations import ReplaceOne

from .azure_client import AzureBlobClient
from .database import db
from .metadata import create, validate_metadata
from .models import DataSource


def env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name, None)
    if value is None:
        return default
    return value.lower() not in ["0", "false", "no", "off"]


async def get(account, container) -> DataSource | None:
    # Get a datasource by account and container
    datasource_collection = db().datasources
    datasource = await datasource_collection.find_one({"account": account, "container": container})
    if datasource is None:
        return None
    return DataSource(**datasource)


async def datasource_exists(account, container) -> bool:
    return await get(account, container) is not None


async def sync(account: str, container: str, awsAccessKeyId: Optional[str] = None):
    print(f"[SYNC] Starting sync for {account}/{container} on PID {os.getpid()} at {time.time()}")
    datasource = await get(account, container)
    if datasource is None:
        print(f"[SYNC] Datasource {account}/{container} does not exist")  # dont raise exception or it will cause unclosed connection errors
        return
    awsAccessKeyId = awsAccessKeyId or datasource.awsAccessKeyId
    azure_blob_client = AzureBlobClient(account, container, awsAccessKeyId, datasource.s3EndpointUrl, datasource.s3VerifySsl)
    if datasource.sasToken:
        azure_blob_client.set_sas_token(decrypt(datasource.sasToken.get_secret_value()))
    if datasource.awsSecretAccessKey:
        azure_blob_client.set_aws_secret_access_key(decrypt(datasource.awsSecretAccessKey.get_secret_value()))

    #######################################
    # Reading and Parsing Local Metafiles #
    #######################################
    if azure_blob_client.account == "local":
        metadatas = []
        for path, subdirs, files in os.walk(azure_blob_client.base_filepath):
            for name in files:
                if name.endswith(".sigmf-meta"):
                    filepath = os.path.join(path, name)
                    if ".." in filepath:
                        raise Exception("Invalid filepath")
                    with open(filepath, "r") as f:
                        content = f.read()
                    try:
                        metadata = json.loads(content)
                        metadata = validate_metadata(metadata)
                    except Exception as e:
                        # this will give specific reasons parsing failed, it eventually needs to get put in a log or something
                        print(f"[SYNC] Error parsing metadata file {filepath}: {e}")
                        continue
                    if metadata:
                        metadatas.append((os.path.join(path, name).replace(azure_blob_client.base_filepath, "")[1:], metadata))
        # Even though the below code is similar to Azure version, the Azure version had to be tweaked to parallelize it
        for metadata in metadatas:
            filepath = metadata[0].replace(".sigmf-meta", "")  # TODO: clean up the tuple messiness
            try:
                if not await azure_blob_client.blob_exist(filepath + ".sigmf-data"):
                    print(f"[SYNC] Data file {filepath} does not exist for metadata file")
                    continue
                metadata = metadata[1]
                metadata["global"]["traceability:origin"] = {
                    "type": "api",
                    "account": account,
                    "container": container,
                    "file_path": filepath,
                }
                metadata["global"]["traceability:revision"] = 0
                file_length = await azure_blob_client.get_file_length(filepath + ".sigmf-data")
                metadata["global"]["traceability:sample_length"] = file_length / get_bytes_per_iq_sample(metadata["global"]["core:datatype"])
                await create(metadata, user=None)  # creates or updates the metadata object
                # print(f"[SYNC] Created metadata for {filepath}") # commented out, caused too much spam
            except Exception as e:
                print(f"[SYNC] Error creating metadata for {filepath}: {e}")

    #########################################################
    # Reading and Parsing Azure or S3 Metafiles in Parallel #
    #########################################################
    else:
        if azure_blob_client.awsAccessKeyId:  # S3
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=azure_blob_client.awsAccessKeyId,
                aws_secret_access_key=azure_blob_client.awsSecretAccessKey.get_secret_value(),
                region_name=azure_blob_client.account,
                endpoint_url=azure_blob_client.s3EndpointUrl,
                verify=azure_blob_client.s3VerifySsl,
                config=Config(s3={"addressing_style": "path"}) if azure_blob_client.s3EndpointUrl else None,
            )
            paginator = s3_client.get_paginator("list_objects_v2")
            meta_blob_names = []
            data_blob_sizes = {}  # holds the names and sizes of sigmf-data files
            start_t = time.time()
            paginate_kwargs = {"Bucket": azure_blob_client.container}
            if datasource.s3Prefix:
                paginate_kwargs["Prefix"] = datasource.s3Prefix
            for page in paginator.paginate(**paginate_kwargs):
                for obj in page.get("Contents", []):
                    blob_name = obj["Key"]
                    if blob_name.endswith(".sigmf-meta"):
                        meta_blob_names.append(blob_name)
                    elif blob_name.endswith(".sigmf-data"):
                        data_blob_sizes[blob_name] = obj["Size"]  # bytes
            if len(meta_blob_names) == 0:
                sample_keys = []
                for page in paginator.paginate(**paginate_kwargs):
                    for obj in page.get("Contents", []):
                        sample_keys.append(obj["Key"])
                        if len(sample_keys) >= 20:
                            break
                    if len(sample_keys) >= 20:
                        break
                print(f"[SYNC] No .sigmf-meta files found. First S3 object keys under prefix {datasource.s3Prefix or '<none>'}: {sample_keys}")

        else:  # Azure blob
            container_client = azure_blob_client.get_container_client()
            blobs = container_client.list_blobs(include=["metadata"])  # cant use list_blob_names because we also need data file length
            meta_blob_names = []
            data_blob_sizes = {}  # holds the names and sizes of sigmf-data files
            start_t = time.time()
            async for blob in blobs:
                if blob.name.endswith(".sigmf-meta"):
                    meta_blob_names.append(blob.name)
                elif blob.name.endswith(".sigmf-data"):
                    data_blob_sizes[blob.name] = blob.size

        print("[SYNC] getting list of metas took", time.time() - start_t, "seconds")  # 30s for MIT
        print("[SYNC] found", len(meta_blob_names), "meta files")  # the process above took about 15s for 36318 metas

        async def get_metadata(meta_blob_name):
            if meta_blob_name.replace(".sigmf-meta", ".sigmf-data") not in data_blob_sizes:  # bail early if data file doesnt exist
                print(f"[SYNC] Data file for {meta_blob_name} wasn't found")
                return None

            # Grab meta contents
            content = await azure_blob_client.get_blob_content(meta_blob_name)
            try:
                metadata = json.loads(content)
                metadata = validate_metadata(metadata)
            except Exception as e:
                # this will give specific reasons parsing failed, it eventually needs to get put in a log or something
                print(f"[SYNC] Error parsing metadata file {meta_blob_name}: {e}")
                return None
            if metadata:
                filepath = meta_blob_name.replace(".sigmf-meta", "")
                # add traceability:origin
                metadata["global"]["traceability:origin"] = {
                    "type": "api",
                    "account": account,
                    "container": container,
                    "file_path": filepath,
                }
                metadata["global"]["traceability:revision"] = 0
                file_length = data_blob_sizes[filepath + ".sigmf-data"]
                bytes_per_iq_sample = get_bytes_per_iq_sample(metadata["global"]["core:datatype"])
                metadata["global"]["traceability:sample_length"] = file_length / bytes_per_iq_sample
                return metadata

        # Running all coroutines at once failed for datasets with 10k's metas, so we need to break it up into batches
        start_t = time.time()
        batch_size = 1000  # manually tweaked, above 1000 it doesnt seem to speed up by much
        num_batches = int(np.ceil(len(meta_blob_names) / batch_size))
        metadatas = []
        for i in range(num_batches):
            coroutines = []
            for meta_blob_name in meta_blob_names[i * batch_size : (i + 1) * batch_size]:
                coroutines.append(get_metadata(meta_blob_name))
            ret = await asyncio.gather(*coroutines)  # Wait for all the coroutines to finish
            metadatas.extend([x for x in ret if x is not None])  # remove the Nones
        print("[SYNC] getting and parsing all metas took", time.time() - start_t, "seconds")

        if Depends(check_access) is None:
            return False

        bulk_writes = []
        for metadata in metadatas:
            meta_name = metadata["global"]["traceability:origin"]["file_path"]
            filter = {
                "global.traceability:origin.account": account,
                "global.traceability:origin.container": container,
                "global.traceability:origin.file_path": meta_name,
            }
            metadata_bson = encode(metadata)
            if len(metadata_bson) > 16793600:
                print(f"[SYNC] Metadata for {meta_name} is too large to store in MongoDB (16MB limit per doc), skipping")
                continue
            bulk_writes.append(ReplaceOne(filter=filter, replacement=RawBSONDocument(metadata_bson), upsert=True))
        metadata_collection = db().metadata
        if bulk_writes:
            await metadata_collection.bulk_write(bulk_writes)

        """ At some point we may remove the versions thing
        # audit document
        audit_document = {
            "metadata": metadata,
            "user": None,
            "action": "create",
        }
        versions: AgnosticCollection = versions_collection()
        await versions.insert_one(audit_document)
        """

    print(f"[SYNC] Finished syncing {account}/{container}")
    await azure_blob_client.close_blob_clients()  # Close all the blob clients to avoid unclosed connection errors


async def create_datasource(datasource: DataSource, user: Optional[dict]) -> bool:
    """
    Create a new datasource. The datasource will be henceforth identified by account/container which
    must be unique or this function will return a 400.
    This will encrypt the sasToken if it is provided.
    """
    datasource_collection = db().datasources
    if await datasource_exists(datasource.account, datasource.container):
        print("Datasource Already Exists!")
        return False
    datasource_dict = datasource.dict(by_alias=True, exclude_unset=True)

    # encrypt takes in the SecretStr and returns a string with the encrypted value, which then gets stored in mongo
    if datasource.sasToken:
        datasource_dict["sasToken"] = encrypt(datasource.sasToken)
    if datasource.accountKey:
        datasource_dict["accountKey"] = encrypt(datasource.accountKey)
    if datasource.awsSecretAccessKey:
        datasource_dict["awsSecretAccessKey"] = encrypt(datasource.awsSecretAccessKey)

    if "owners" not in datasource_dict:
        datasource_dict["owners"] = []
    if user and user["preferred_username"]:
        datasource_dict["owners"].append(user["preferred_username"])
    if "readers" not in datasource_dict:
        datasource_dict["readers"] = []
    if "public" not in datasource_dict:
        datasource_dict["public"] = True

    await datasource_collection.insert_one(datasource_dict)
    return True


async def import_datasources_from_env():
    connection_info = os.getenv("IQENGINE_CONNECTION_INFO", None)
    base_filepath = os.getenv("IQENGINE_BACKEND_LOCAL_FILEPATH", None)
    base_filepath = base_filepath.replace('"', "") if base_filepath else None
    minio_endpoint_url = os.getenv("MINIO_ENDPOINT_URL", None)
    minio_bucket = os.getenv("MINIO_BUCKET", None)
    minio_access_key = os.getenv("MINIO_ACCESS_KEY", None)
    minio_secret_key = os.getenv("MINIO_SECRET_KEY", None)

    # For those using MSAL to enter in datasource connection info, leave IQENGINE_CONNECTION_INFO and IQENGINE_BACKEND_LOCAL_FILEPATH empty
    if not connection_info and not base_filepath and not (minio_endpoint_url and minio_bucket and minio_access_key and minio_secret_key):
        return

    # Clear the db
    metadata_collection = db().metadata
    await metadata_collection.delete_many({})  # clears the metadata db

    datasource_collection = db().datasources
    await datasource_collection.delete_many({})  # clears the datasource db

    # Add datasource corresponding to the local backend storage
    if base_filepath and os.path.exists(base_filepath):
        try:
            datasource = DataSource(
                account="local",
                container="local",
                sasToken=None,
                accountKey=None,
                name="Local to Backend",
                description="Files stored on the backend server in the " + str(base_filepath) + " directory",
                imageURL="https://raw.githubusercontent.com/IQEngine/IQEngine/main/client/public/backend-storage.svg",
                type="api",
                public=True,
                owners=["IQEngine-Admin"],
                readers=[],
            )
            create_ret = await create_datasource(datasource=datasource, user=None)
            if create_ret:
                await sync("local", "local", None)
        except Exception as e:
            print("Failed to import datasource local to backend", e)

    # Add all cloud datasources
    if connection_info:
        for connection in json.loads(connection_info).get("settings", []):
            try:
                datasource = DataSource(
                    account=connection["accountName"],
                    container=connection["containerName"],
                    awsAccessKeyId=connection["awsAccessKeyId"] if "awsAccessKeyId" in connection else None,
                    s3EndpointUrl=connection["s3EndpointUrl"] if "s3EndpointUrl" in connection else None,
                    s3Prefix=connection["s3Prefix"] if "s3Prefix" in connection else None,
                    s3VerifySsl=connection["s3VerifySsl"] if "s3VerifySsl" in connection else None,
                    sasToken=SecretStr(connection["sasToken"]) if "sasToken" in connection else None,
                    accountKey=SecretStr(connection["accountKey"]) if "accountKey" in connection else None,
                    awsSecretAccessKey=SecretStr(connection["awsSecretAccessKey"]) if "awsSecretAccessKey" in connection else None,
                    name=connection["name"],
                    description=connection["description"] if "description" in connection else None,
                    imageURL=connection["imageURL"] if "imageURL" in connection else None,
                    type="api",
                    public=connection["public"] if "public" in connection else True,
                    owners=connection["owners"] if "owners" in connection else ["IQEngine-Admin"],
                    readers=connection["readers"] if "readers" in connection else [],
                )
                create_ret = await create_datasource(datasource=datasource, user=None)
                if create_ret:
                    await sync(connection["accountName"], connection["containerName"], connection.get("awsAccessKeyId", None))
            except Exception as e:
                print(f"Failed to import datasource {connection['name']}.", e)
                continue

    if minio_endpoint_url and minio_bucket and minio_access_key and minio_secret_key:
        minio_region = os.getenv("MINIO_REGION", "us-east-1")
        minio_prefix = os.getenv("MINIO_PREFIX", None) or None
        minio_verify_ssl = env_bool("MINIO_VERIFY_SSL", True)
        try:
            datasource = DataSource(
                account=minio_region,
                container=minio_bucket,
                awsAccessKeyId=minio_access_key,
                s3EndpointUrl=minio_endpoint_url,
                s3Prefix=minio_prefix,
                s3VerifySsl=minio_verify_ssl,
                awsSecretAccessKey=SecretStr(minio_secret_key),
                name=os.getenv("MINIO_DATASOURCE_NAME", "FAST MinIO SigMF"),
                description=os.getenv("MINIO_DATASOURCE_DESCRIPTION", f"SigMF recordings from {minio_bucket} on FAST MinIO"),
                imageURL=os.getenv("MINIO_DATASOURCE_IMAGE_URL", None),
                type="api",
                public=True,
                owners=["IQEngine-Admin"],
                readers=[],
            )
            create_ret = await create_datasource(datasource=datasource, user=None)
            if create_ret:
                await sync(minio_region, minio_bucket, minio_access_key)
        except Exception as e:
            print("Failed to import datasource FAST MinIO SigMF.", e)
