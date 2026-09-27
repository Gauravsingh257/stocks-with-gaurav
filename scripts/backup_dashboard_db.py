"""
scripts/backup_dashboard_db.py — off-volume backup of dashboard.db.

    python -m scripts.backup_dashboard_db              # backup + upload + prune
    python -m scripts.backup_dashboard_db --no-upload  # local copy only (testing)

WHY THIS SHAPE
--------------
dashboard.db (~2.9 GB, 2026-09-27) lives on the web service's 4.6 GB volume with
~1.8 GB free, so a backup copy cannot be written beside it — a full volume stops
SQLite writes and takes the web service down. The copy is therefore taken into the
container's ephemeral disk (BACKUP_TMP_DIR, default /tmp), compressed, uploaded to
the Railway bucket `swg-db-backups` (S3 API, same project), and deleted locally.

CONSISTENCY
-----------
SQLite's online-backup API in ONE step (`pages=-1`): a single read transaction, so
the copy is one consistent snapshot while web keeps writing (WAL: readers never
block writers). A chunked backup restarts whenever another connection writes, and
with the scanners writing continuously it might never finish.

RUN OUT OF BAND — the daily scheduler job spawns this as a subprocess. Never call
it from a request handler or a FastAPI startup hook (the 2026-08-02 outage rule).

Env: BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, BACKUP_S3_REGION, BACKUP_S3_ACCESS_KEY_ID,
BACKUP_S3_SECRET_ACCESS_KEY, BACKUP_S3_ADDRESSING (virtual|path),
BACKUP_RETENTION_DAYS (14), BACKUP_TMP_DIR (/tmp).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("backup_dashboard_db")

IST = timezone(timedelta(hours=5, minutes=30))
PREFIX = "dashboard-db/"
KEY_FMT = "dashboard-%Y%m%d-%H%M%S"
MIN_KEEP = 3  # never prune below this many backups, whatever their age


def s3_client():
    import boto3  # noqa: PLC0415 — only the web image needs it
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        "s3",
        endpoint_url=os.environ["BACKUP_S3_ENDPOINT"],
        region_name=os.getenv("BACKUP_S3_REGION", "auto"),
        aws_access_key_id=os.environ["BACKUP_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["BACKUP_S3_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": os.getenv("BACKUP_S3_ADDRESSING", "virtual")},
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def bucket() -> str:
    return os.environ["BACKUP_S3_BUCKET"]


def source_db_path() -> Path:
    from dashboard.backend.db.schema import DB_PATH  # noqa: PLC0415

    return Path(DB_PATH)


# ── pure steps (unit-tested) ──────────────────────────────────────────────────

def online_backup(src: Path, dst: Path) -> None:
    """Consistent snapshot of `src` into `dst` in a single backup step."""
    s = sqlite3.connect(str(src), timeout=60)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d, pages=-1)
    finally:
        d.close()
        s.close()


def quick_check(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return str(conn.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        conn.close()


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(path))
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: int(conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]) for n in names}
    finally:
        conn.close()


def gzip_file(src: Path, dst: Path) -> str:
    """Compress src → dst; returns the sha256 of the compressed file."""
    h = hashlib.sha256()
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
    with open(dst, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stamp_of(key: str) -> datetime | None:
    name = key.rsplit("/", 1)[-1].split(".", 1)[0]
    try:
        return datetime.strptime(name, KEY_FMT).replace(tzinfo=IST)
    except ValueError:
        return None


def list_backups(client, bkt: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kw = {"Bucket": bkt, "Prefix": PREFIX}
        if token:
            kw["ContinuationToken"] = token
        resp = client.list_objects_v2(**kw)
        keys += [o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith(".db.gz")]
        if not resp.get("IsTruncated"):
            return sorted(keys)
        token = resp.get("NextContinuationToken")


def prune(client, bkt: str, now: datetime, retention_days: int, keep_key: str) -> list[str]:
    """Delete backups (and their manifests) older than the retention window,
    always keeping the newest MIN_KEEP and the one just written."""
    keys = list_backups(client, bkt)
    newest = set(keys[-MIN_KEEP:]) | {keep_key}
    cutoff = now - timedelta(days=retention_days)
    doomed = [k for k in keys if k not in newest and (stamp_of(k) or now) < cutoff]
    for k in doomed:
        client.delete_object(Bucket=bkt, Key=k)
        client.delete_object(Bucket=bkt, Key=k.replace(".db.gz", ".manifest.json"))
    return doomed


# ── orchestration ─────────────────────────────────────────────────────────────

def run(*, src: Path, upload: bool, client=None, bkt: str | None = None, now: datetime | None = None,
        tmp_root: str | None = None, retention_days: int | None = None) -> dict:
    now = now or datetime.now(IST)
    retention_days = retention_days or int(os.getenv("BACKUP_RETENTION_DAYS", "14"))
    tmp_root = tmp_root or os.getenv("BACKUP_TMP_DIR", "/tmp")
    src_size = src.stat().st_size
    free = shutil.disk_usage(tmp_root).free
    if free < src_size * 2.2:
        raise RuntimeError(f"not enough scratch space in {tmp_root}: {free} free for a {src_size}-byte database")

    name = now.strftime(KEY_FMT)
    key = f"{PREFIX}{name}.db.gz"
    work = Path(tempfile.mkdtemp(prefix="dbbackup-", dir=tmp_root))
    report: dict = {"key": key, "source": str(src), "source_bytes": src_size, "started_at": now.isoformat()}
    try:
        t0 = time.time()
        copy = work / f"{name}.db"
        online_backup(src, copy)
        report["backup_sec"] = round(time.time() - t0, 1)

        check = quick_check(copy)
        if check != "ok":
            raise RuntimeError(f"quick_check failed on the backup copy: {check[:200]}")
        report["quick_check"] = check
        report["table_counts"] = table_counts(copy)
        report["copy_bytes"] = copy.stat().st_size

        t1 = time.time()
        gz = work / f"{name}.db.gz"
        report["sha256"] = gzip_file(copy, gz)
        report["gzip_bytes"] = gz.stat().st_size
        report["gzip_sec"] = round(time.time() - t1, 1)
        copy.unlink()

        if upload:
            client = client or s3_client()
            bkt = bkt or bucket()
            t2 = time.time()
            client.upload_file(str(gz), bkt, key, ExtraArgs={"Metadata": {"sha256": report["sha256"]}})
            report["upload_sec"] = round(time.time() - t2, 1)
            manifest = {k: v for k, v in report.items()}
            client.put_object(Bucket=bkt, Key=key.replace(".db.gz", ".manifest.json"),
                              Body=json.dumps(manifest, indent=1).encode("utf-8"),
                              ContentType="application/json")
            report["pruned"] = prune(client, bkt, now, retention_days, key)
            report["retained"] = len(list_backups(client, bkt))
        else:
            report["local_path"] = str(gz)
        report["ok"] = True
        return report
    finally:
        if upload:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-upload", action="store_true", help="write the compressed copy locally and stop")
    args = ap.parse_args()
    try:
        report = run(src=source_db_path(), upload=not args.no_upload)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        return 1
    slim = {k: v for k, v in report.items() if k != "table_counts"}
    slim["tables"] = len(report.get("table_counts", {}))
    print(json.dumps(slim, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
