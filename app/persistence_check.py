import io
import os
import sys

from app.storage import postgres_connection, mongo_client, minio_client

mode = sys.argv[1]
if mode not in {"seed", "verify"}:
    raise SystemExit("Usage: python -m app.persistence_check seed|verify")

marker = "checkpoint-c-persistence-v1"
bucket = "checkpoint-c"
object_name = "marker.txt"
payload = marker.encode("utf-8")

with postgres_connection() as connection:
    if mode == "seed":
        connection.execute(
            "CREATE TABLE IF NOT EXISTS checkpoint_c "
            "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO checkpoint_c (id, value) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value",
            (marker,),
        )
    row = connection.execute(
        "SELECT value FROM checkpoint_c WHERE id = 1"
    ).fetchone()
    if row != (marker,):
        raise RuntimeError("PostgreSQL persistence check failed")
print("PostgreSQL: PASS")

with mongo_client() as client:
    collection = client[os.environ["MONGO_DB"]]["checkpoint_c"]
    if mode == "seed":
        collection.replace_one(
            {"_id": "marker"},
            {"_id": "marker", "value": marker},
            upsert=True,
        )
    document = collection.find_one({"_id": "marker"})
    if not document or document.get("value") != marker:
        raise RuntimeError("MongoDB persistence check failed")
print("MongoDB: PASS")

client = minio_client()
if mode == "seed":
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(
        bucket,
        object_name,
        io.BytesIO(payload),
        len(payload),
        content_type="text/plain",
    )

response = client.get_object(bucket, object_name)
try:
    actual = response.read()
finally:
    response.close()
    response.release_conn()

if actual != payload:
    raise RuntimeError("MinIO persistence check failed")
print("MinIO: PASS")
print(f"Persistence {mode}: PASS")
