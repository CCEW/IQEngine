import os
import socket
from pathlib import Path

import pymongo_inmemory
import pymongo_inmemory.context
from motor.core import AgnosticDatabase
from motor.motor_asyncio import AsyncIOMotorClient

# IQEngine supports either connecting to an existing MongoDB instance or using an in-memory database (meant primarily for testing or local dev)

_db: AgnosticDatabase = None
in_memory_db: pymongo_inmemory.MongoClient = None


def get_open_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def create_db_client() -> AgnosticDatabase:
    global _db
    connection_string = os.getenv("IQENGINE_METADATA_DB_CONNECTION_STRING")
    _db = AsyncIOMotorClient(connection_string)["IQEngine"]
    return _db


def create_in_memory_db_client() -> AgnosticDatabase:
    global _db, in_memory_db
    pymongo_inmemory.context.CACHE_FOLDER = str(Path.cwd().parent / ".pytest-pymongo")
    os.environ.setdefault("PYMONGOIM__STORAGE_ENGINE", "wiredTiger")
    in_memory_db = pymongo_inmemory.MongoClient(port=get_open_port())
    _db = AsyncIOMotorClient(in_memory_db._mongod.connection_string)["IQEngine"]
    return _db


def db() -> AgnosticDatabase:
    global _db
    if _db is None:
        if "IN_MEMORY_DB" in os.environ and os.environ["IN_MEMORY_DB"] != str("0"):
            _db = create_in_memory_db_client()
        else:
            _db = create_db_client()
    return _db


async def reset_db():
    global _db, in_memory_db
    if _db is None:
        return
    if "IN_MEMORY_DB" in os.environ and os.environ["IN_MEMORY_DB"] != str("0"):
        await _db.client.drop_database("IQEngine")
    _db.client.close()
    if in_memory_db is not None:
        in_memory_db.close()
        in_memory_db = None
    _db = None
