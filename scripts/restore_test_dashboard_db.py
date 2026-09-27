"""
scripts/restore_test_dashboard_db.py — prove a dashboard.db backup restores.

    python -m scripts.restore_test_dashboard_db            # latest backup
    python -m scripts.restore_test_dashboard_db --key dashboard-db/dashboard-20260927-150000.db.gz
    python -m scripts.restore_test_dashboard_db --keep     # leave the restored copy in the scratch dir

Downloads a backup into a SCRATCH directory (default /tmp — never the live volume),
verifies the SHA-256 recorded at backup time, decompresses, runs PRAGMA
quick_check, and compares every table's row count with the backup manifest (must
match exactly) and with the live database (reported; live tables only grow).
Read-only with respect to production data. Exit code 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.backup_dashboard_db import (  # noqa: E402
    bucket,
    list_backups,
    quick_check,
    s3_client,
    source_db_path,
    table_counts,
)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def restore_test(client, bkt: str, key: str | None, scratch_root: str, live: Path | None,
                 keep: bool = False) -> dict:
    key = key or list_backups(client, bkt)[-1]
    manifest = json.loads(client.get_object(Bucket=bkt, Key=key.replace(".db.gz", ".manifest.json"))["Body"].read())
    work = Path(tempfile.mkdtemp(prefix="dbrestore-", dir=scratch_root))
    out: dict = {"key": key, "scratch": str(work)}
    try:
        gz = work / "backup.db.gz"
        t0 = time.time()
        client.download_file(bkt, key, str(gz))
        out["download_sec"] = round(time.time() - t0, 1)
        out["sha256_ok"] = sha256_of(gz) == manifest["sha256"]
        if not out["sha256_ok"]:
            out["ok"] = False  # never decompress an unverified file
            return out

        db = work / "restored.db"
        t1 = time.time()
        with gzip.open(gz, "rb") as fi, open(db, "wb") as fo:
            shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)
        gz.unlink()
        out["decompress_sec"] = round(time.time() - t1, 1)
        out["restored_bytes"] = db.stat().st_size
        out["quick_check"] = quick_check(db)

        restored = table_counts(db)
        expected = manifest["table_counts"]
        out["tables"] = len(restored)
        out["manifest_mismatches"] = {t: [expected.get(t), restored.get(t)]
                                      for t in set(expected) | set(restored) if expected.get(t) != restored.get(t)}
        if live is not None:
            now = table_counts(live)
            out["live_vs_backup"] = {t: now.get(t, 0) - restored.get(t, 0) for t in sorted(restored)
                                     if now.get(t, 0) != restored.get(t, 0)}
            out["live_shrunk"] = {t: d for t, d in out["live_vs_backup"].items() if d < 0}
        out["key_tables"] = {t: restored.get(t) for t in (
            "trades", "portfolio_positions", "portfolio_journal", "momentum_positions", "momentum_journal",
            "trade_lifecycle", "research_track_record", "position_provenance", "signals_log",
            "forward_returns", "stock_universe", "sector_rotation_daily") if t in restored}
        out["ok"] = bool(out["sha256_ok"] and out["quick_check"] == "ok" and not out["manifest_mismatches"])
        return out
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key")
    ap.add_argument("--scratch", default=os.getenv("BACKUP_TMP_DIR", "/tmp"))
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--no-live", action="store_true", help="skip the comparison with the live database")
    args = ap.parse_args()
    res = restore_test(s3_client(), bucket(), args.key, args.scratch,
                       None if args.no_live else source_db_path(), keep=args.keep)
    print(json.dumps(res, indent=1, default=str))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
