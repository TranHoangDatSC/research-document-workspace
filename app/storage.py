import os

import psycopg
import urllib3
from minio import Minio
from pymongo import MongoClient


def postgres_connection():
    return psycopg.connect(
        host="postgres",
        port=5432,
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        connect_timeout=3,
        options="-c statement_timeout=3000",
    )


def mongo_client():
    return MongoClient(
        host="mongo",
        port=27017,
        username=os.environ["MONGO_INITDB_ROOT_USERNAME"],
        password=os.environ["MONGO_INITDB_ROOT_PASSWORD"],
        authSource="admin",
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=3000,
    )


def minio_client():
    return Minio(
        "minio:9000",
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=False,
        http_client=urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=3, read=3),
            retries=False,
        ),
    )
