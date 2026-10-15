"""CloudVault processing worker.

Triggered by SQS (fed by S3 ObjectCreated events on uploads/). Each file goes
through one streaming pass: validate -> scan -> checksum -> text extraction ->
gzip compression, plus an optional thumbnail when Pillow is available.
Failures are retried by SQS; after MAX_RECEIVES the job lands in the DLQ and
the file is marked FAILED.
"""
import gzip
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

log = logging.getLogger()
log.setLevel(logging.INFO)

HEAD_BYTES = 4096
SAMPLE_BYTES = 64 * 1024
SEARCH_CHARS = 20_000
MAX_RECEIVES = int(os.environ.get("MAX_RECEIVES", 3))

# The standard (harmless) EICAR antivirus test string, split so this source file
# itself does not trigger desktop antivirus.
EICAR = (b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*")

SIGNATURES = [  # (magic bytes, type, mime)
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"%PDF-", "pdf", "application/pdf"),
    (b"PK\x03\x04", "zip", "application/zip"),
    (b"\x1f\x8b", "gz", "application/gzip"),
    (b"MZ", "exe", "application/x-msdownload"),
    (b"\x7fELF", "elf", "application/x-elf"),
]
BLOCKED_TYPES = {"exe", "elf"}
EXT_ALIASES = {"jpeg": "jpg", "docx": "zip", "xlsx": "zip", "pptx": "zip", "jar": "zip"}
STRICT_EXTS = {"png", "jpg", "gif", "pdf"}  # extension must match magic bytes
TEXT_EXTS = {"txt", "md", "csv", "json", "log", "xml", "yml", "yaml", "html", "py", "js", "ini"}

_cache = {}


def _s3():
    if "s3" not in _cache:
        _cache["s3"] = boto3.client("s3")
    return _cache["s3"]


def _table():
    if "t" not in _cache:
        _cache["t"] = boto3.resource("dynamodb").Table(os.environ["FILES_TABLE"])
    return _cache["t"]


def notify(subject, message):
    topic = os.environ.get("TOPIC_ARN")
    if not topic:
        return
    try:
        if "sns" not in _cache:
            _cache["sns"] = boto3.client("sns")
        _cache["sns"].publish(TopicArn=topic, Subject=subject[:100], Message=message)
    except Exception:
        log.exception("notify failed")


# ------------------------------------------------------------------ pure logic (unit tested)
def detect_type(head, filename):
    for magic, kind, _mime in SIGNATURES:
        if head.startswith(magic):
            return kind
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError as e:
        if e.start >= len(head) - 3 and len(head) >= HEAD_BYTES:  # cut mid-character
            return "text"
    return "binary"


def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def check_extension(detected, filename):
    """Return a warning string if the extension contradicts the real content."""
    ext = EXT_ALIASES.get(ext_of(filename), ext_of(filename))
    if ext in STRICT_EXTS and detected != ext:
        return f"extension .{ext_of(filename)} does not match detected type '{detected}'"
    return None


def analyze_stream(chunks, gz_path=None):
    """Single pass over the file: checksum, size, head/sample bytes, EICAR scan, gzip."""
    sha, size, head, sample, tail, infected = hashlib.sha256(), 0, b"", b"", b"", False
    gz = gzip.open(gz_path, "wb", compresslevel=6) if gz_path else None
    try:
        for chunk in chunks:
            sha.update(chunk)
            size += len(chunk)
            if len(head) < HEAD_BYTES:
                head += chunk[: HEAD_BYTES - len(head)]
            if len(sample) < SAMPLE_BYTES:
                sample += chunk[: SAMPLE_BYTES - len(sample)]
            if not infected:
                window = tail + chunk
                infected = EICAR in window
                tail = window[-(len(EICAR) - 1):]
            if gz:
                gz.write(chunk)
    finally:
        if gz:
            gz.close()
    return {"sha256": sha.hexdigest(), "size": size, "head": head, "sample": sample, "infected": infected}


def search_text(sample, detected, filename):
    if detected != "text" and ext_of(filename) not in TEXT_EXTS:
        return ""
    return " ".join(sample.decode("utf-8", "ignore").lower().split())[:SEARCH_CHARS]


def parse_key(key):
    """uploads/<user_id>/<file_id>/<filename> -> (user_id, file_id, filename) or None."""
    parts = key.split("/", 3)
    if len(parts) != 4 or parts[0] != "uploads" or not all(parts[1:]):
        return None
    return parts[1], parts[2], parts[3]


def keys_from_record(record):
    body = json.loads(record["body"])
    if body.get("Event") == "s3:TestEvent":
        return []
    return [unquote_plus(r["s3"]["object"]["key"]) for r in body.get("Records", [])]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ AWS side
def update(user_id, file_id, **fields):
    names = {f"#{k}": k for k in fields}
    values = {f":{k}": v for k, v in fields.items()}
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    _table().update_item(Key={"user_id": user_id, "file_id": file_id}, UpdateExpression=expr,
                         ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def make_thumbnail(bucket, key, user_id, file_id):
    try:
        from PIL import Image  # optional layer
    except ImportError:
        return None
    import io
    obj = _s3().get_object(Bucket=bucket, Key=key)
    if obj["ContentLength"] > 25 * 1024 * 1024:
        return None
    img = Image.open(io.BytesIO(obj["Body"].read()))
    img.thumbnail((256, 256))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    thumb_key = f"processed/{user_id}/{file_id}/thumbnail.jpg"
    _s3().put_object(Bucket=bucket, Key=thumb_key, Body=buf.getvalue(), ContentType="image/jpeg")
    return thumb_key


def quarantine(bucket, key, user_id, file_id, filename):
    new_key = f"quarantine/{user_id}/{file_id}/{filename}"
    _s3().copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=new_key)
    _s3().delete_object(Bucket=bucket, Key=key)
    return new_key


def process_key(bucket, key):
    parsed = parse_key(key)
    if not parsed:
        log.info("skipping non-upload key %s", key)
        return
    user_id, file_id, filename = parsed
    started = time.time()
    update(user_id, file_id, status="PROCESSING")

    body = _s3().get_object(Bucket=bucket, Key=key)["Body"]
    with tempfile.NamedTemporaryFile(suffix=".gz", dir="/tmp") as tmp:
        result = analyze_stream(body.iter_chunks(1024 * 1024), gz_path=tmp.name)
        detected = detect_type(result["head"], filename)

        if result["infected"] or detected in BLOCKED_TYPES:
            reason = "malware signature found" if result["infected"] else f"blocked file type '{detected}'"
            new_key = quarantine(bucket, key, user_id, file_id, filename)
            update(user_id, file_id, status="QUARANTINED" if result["infected"] else "BLOCKED",
                   key=new_key, sha256=result["sha256"], error=reason, processed_at=now_iso())
            notify("CloudVault: file rejected", f"{filename} ({user_id}) was rejected: {reason}")
            return

        compressed_key = f"processed/{user_id}/{file_id}/{filename}.gz"
        compressed_size = os.path.getsize(tmp.name)
        _s3().upload_file(tmp.name, bucket, compressed_key, ExtraArgs={"ContentType": "application/gzip"})

    fields = {
        "status": "DONE", "sha256": result["sha256"], "size": result["size"], "detected_type": detected,
        "compressed_key": compressed_key, "compressed_size": compressed_size,
        "search_text": search_text(result["sample"], detected, filename), "processed_at": now_iso(),
        "has_thumbnail": False,
    }
    warning = check_extension(detected, filename)
    if warning:
        fields["warnings"] = [warning]
    if detected in ("png", "jpg", "gif"):
        thumb = make_thumbnail(bucket, key, user_id, file_id)
        if thumb:
            fields.update(thumbnail_key=thumb, has_thumbnail=True)
    update(user_id, file_id, **fields)
    log.info("processed %s in %.2fs", key, time.time() - started)
    notify("CloudVault: file processed", f"{filename} is ready (sha256 {result['sha256'][:12]}...)")


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            for key in keys_from_record(record):
                process_key(os.environ["BUCKET"], key)
        except Exception as exc:
            log.exception("job failed: %s", record.get("messageId"))
            receives = int(record.get("attributes", {}).get("ApproximateReceiveCount", 1))
            if receives >= MAX_RECEIVES:
                for key in keys_from_record(record):
                    parsed = parse_key(key)
                    if parsed:
                        update(parsed[0], parsed[1], status="FAILED", error=str(exc)[:300])
                        notify("CloudVault: processing failed", f"{parsed[2]} failed after {receives} attempts: {exc}")
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
