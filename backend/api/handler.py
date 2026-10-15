"""CloudVault API Lambda (HTTP API payload v2.0).

Routes are wired in infra/main.tf. Authenticated routes get the Cognito user id
from the JWT authorizer; /s/* routes are public and guarded by share tokens.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config
from botocore.exceptions import ClientError

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
FINAL_STATES = {"DONE", "FAILED", "QUARANTINED", "BLOCKED"}
PBKDF2_ROUNDS = 200_000

_cache = {}


# ------------------------------------------------------------------ aws helpers
def _s3():
    if "s3" not in _cache:
        _cache["s3"] = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _cache["s3"]


def _ddb():
    if "ddb" not in _cache:
        _cache["ddb"] = boto3.resource("dynamodb")
    return _cache["ddb"]


def files_table():
    return _ddb().Table(os.environ["FILES_TABLE"])


def shares_table():
    return _ddb().Table(os.environ["SHARES_TABLE"])


def notify(subject, message):
    topic = os.environ.get("TOPIC_ARN")
    if not topic:
        return
    try:
        if "sns" not in _cache:
            _cache["sns"] = boto3.client("sns")
        _cache["sns"].publish(TopicArn=topic, Subject=subject[:100], Message=message)
    except Exception:  # notifications must never break a request
        log.exception("notify failed")


# ------------------------------------------------------------------ pure helpers
def sanitize_filename(name):
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    name = re.sub(r"[^A-Za-z0-9._ \-()]", "_", name)
    name = re.sub(r"\.{2,}", ".", name).lstrip(".")
    return name[:150]


def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$")
        candidate = hash_password(password, bytes.fromhex(salt_hex)).split("$")[1]
        return hmac.compare_digest(candidate, digest_hex)
    except Exception:
        return False


def clamp(value, low, high, default):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def respond(status, body=None):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body if body is not None else {}, cls=_Enc),
    }


def err(status, code, message=None):
    return respond(status, {"error": code, "message": message or code})


def body_of(event):
    raw = event.get("body") or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def public_file(item):
    keys = ("file_id", "filename", "size", "content_type", "status", "created_at",
            "processed_at", "sha256", "detected_type", "compressed_size", "has_thumbnail",
            "warnings", "error")
    return {k: item[k] for k in keys if k in item}


def public_share(item):
    keys = ("token", "file_id", "created_at", "expires_at", "downloads", "max_downloads")
    out = {k: item[k] for k in keys if k in item}
    out["password_protected"] = "password_hash" in item
    return out


# ------------------------------------------------------------------ authenticated routes
def upload_url(user, event):
    data = body_of(event)
    filename = sanitize_filename(data.get("filename"))
    content_type = str(data.get("content_type") or "application/octet-stream")[:100]
    size = clamp(data.get("size"), 0, 10**12, 0)
    if not filename:
        return err(400, "invalid_filename")
    if size <= 0 or size > MAX_UPLOAD_BYTES:
        return err(400, "invalid_size", f"File must be 1 byte to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    file_id = uuid.uuid4().hex
    key = f"uploads/{user['id']}/{file_id}/{filename}"
    files_table().put_item(Item={
        "user_id": user["id"], "file_id": file_id, "filename": filename, "size": size,
        "content_type": content_type, "status": "PENDING_UPLOAD", "created_at": now_iso(),
        "owner_email": user.get("email", ""), "key": key,
    })
    post = _s3().generate_presigned_post(
        Bucket=os.environ["BUCKET"], Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[{"Content-Type": content_type}, ["content-length-range", 1, MAX_UPLOAD_BYTES]],
        ExpiresIn=900,
    )
    return respond(201, {"file_id": file_id, "upload": post})


def list_files(user, event):
    q = ((event.get("queryStringParameters") or {}).get("q") or "").strip().lower()
    items, kwargs = [], {"KeyConditionExpression": Key("user_id").eq(user["id"])}
    while True:
        page = files_table().query(**kwargs)
        items += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if q:
        items = [i for i in items if q in i.get("filename", "").lower() or q in i.get("search_text", "")]
    items.sort(key=lambda i: i.get("created_at", ""), reverse=True)
    return respond(200, {"files": [public_file(i) for i in items]})


def _own_file(user, file_id):
    return files_table().get_item(Key={"user_id": user["id"], "file_id": file_id}).get("Item")


def get_file(user, event):
    item = _own_file(user, event["pathParameters"]["file_id"])
    if not item:
        return err(404, "not_found")
    shares = shares_table().query(IndexName="by_file", KeyConditionExpression=Key("file_id").eq(item["file_id"]))["Items"]
    now = int(time.time())
    live = [public_share(s) for s in shares if int(s.get("expires_at", 0)) > now]
    return respond(200, {"file": public_file(item), "shares": live})


def download(user, event):
    item = _own_file(user, event["pathParameters"]["file_id"])
    if not item:
        return err(404, "not_found")
    if item["status"] != "DONE":
        return err(409, "not_ready", f"File status is {item['status']}")
    variant = (event.get("queryStringParameters") or {}).get("variant", "original")
    key = {"original": item["key"], "compressed": item.get("compressed_key"),
           "thumbnail": item.get("thumbnail_key")}.get(variant)
    if not key:
        return err(404, "variant_unavailable")
    return respond(200, {"url": _presign_get(key, item["filename"], variant), "filename": item["filename"]})


def _presign_get(key, filename, variant="original"):
    name = filename + (".gz" if variant == "compressed" else "")
    return _s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": os.environ["BUCKET"], "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{name}"'},
        ExpiresIn=60,
    )


def delete_file(user, event):
    file_id = event["pathParameters"]["file_id"]
    if not _own_file(user, file_id):
        return err(404, "not_found")
    s3, bucket = _s3(), os.environ["BUCKET"]
    for prefix in ("uploads", "processed", "quarantine"):
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": f"{prefix}/{user['id']}/{file_id}/"}
            if token:
                kw["ContinuationToken"] = token
            page = s3.list_objects_v2(**kw)
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            token = page.get("NextContinuationToken")
            if not token:
                break
    for s in shares_table().query(IndexName="by_file", KeyConditionExpression=Key("file_id").eq(file_id))["Items"]:
        shares_table().delete_item(Key={"token": s["token"]})
    files_table().delete_item(Key={"user_id": user["id"], "file_id": file_id})
    return respond(200, {"deleted": file_id})


def create_share(user, event):
    item = _own_file(user, event["pathParameters"]["file_id"])
    if not item:
        return err(404, "not_found")
    if item["status"] != "DONE":
        return err(409, "not_ready", "Only processed files can be shared")
    data = body_of(event)
    hours = clamp(data.get("expires_in_hours"), 1, 24 * 7, 24)
    share = {
        "token": secrets.token_urlsafe(24), "file_id": item["file_id"], "owner_id": user["id"],
        "created_at": now_iso(), "expires_at": int(time.time()) + hours * 3600, "downloads": 0,
    }
    if data.get("max_downloads") not in (None, "", 0):
        share["max_downloads"] = clamp(data["max_downloads"], 1, 10_000, 1)
    password = str(data.get("password") or "")
    if password:
        share["password_hash"] = hash_password(password)
    shares_table().put_item(Item=share)
    return respond(201, {"share": public_share(share)})


def revoke_share(user, event):
    token = event["pathParameters"]["token"]
    try:
        shares_table().delete_item(
            Key={"token": token},
            ConditionExpression=Attr("owner_id").eq(user["id"]),
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return err(404, "not_found")
        raise
    return respond(200, {"revoked": token})


# ------------------------------------------------------------------ public share routes
def _live_share(token):
    share = shares_table().get_item(Key={"token": token}).get("Item")
    if not share or int(share.get("expires_at", 0)) <= int(time.time()):
        return None, None
    file_item = files_table().get_item(Key={"user_id": share["owner_id"], "file_id": share["file_id"]}).get("Item")
    if not file_item or file_item["status"] != "DONE":
        return None, None
    return share, file_item


def share_info(event):
    share, f = _live_share(event["pathParameters"]["token"])
    if not share:
        return err(404, "link_invalid", "This link is invalid, expired or revoked")
    return respond(200, {
        "filename": f["filename"], "size": f["size"],
        "password_required": "password_hash" in share,
        "expires_at": share["expires_at"],
    })


def share_download(event):
    token = event["pathParameters"]["token"]
    share, f = _live_share(token)
    if not share:
        return err(404, "link_invalid", "This link is invalid, expired or revoked")
    if "password_hash" in share and not verify_password(str(body_of(event).get("password") or ""), share["password_hash"]):
        return err(401, "wrong_password", "Wrong or missing password")
    try:
        shares_table().update_item(
            Key={"token": token},
            UpdateExpression="ADD downloads :one",
            ConditionExpression="attribute_not_exists(max_downloads) OR downloads < max_downloads",
            ExpressionAttributeValues={":one": 1},
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return err(410, "limit_reached", "Download limit reached")
        raise
    notify("CloudVault: shared file downloaded", f"{f['filename']} was downloaded via share link {token[:6]}...")
    return respond(200, {"url": _presign_get(f["key"], f["filename"]), "filename": f["filename"]})


# ------------------------------------------------------------------ router
ROUTES = {
    "GET /files": list_files,
    "POST /files/upload-url": upload_url,
    "GET /files/{file_id}": get_file,
    "DELETE /files/{file_id}": delete_file,
    "GET /files/{file_id}/download": download,
    "POST /files/{file_id}/share": create_share,
    "DELETE /shares/{token}": revoke_share,
}
PUBLIC = {"GET /s/{token}": share_info, "POST /s/{token}/download": share_download}


def handler(event, context):
    route = event.get("routeKey")
    try:
        if route in PUBLIC:
            return PUBLIC[route](event)
        fn = ROUTES.get(route)
        if not fn:
            return err(404, "no_route")
        claims = event["requestContext"]["authorizer"]["jwt"]["claims"]
        return fn({"id": claims["sub"], "email": claims.get("email", "")}, event)
    except Exception:
        log.exception("unhandled error on %s", route)
        return err(500, "internal_error")
