"""dashboard.db backup + restore test (Phase 3) — local SQLite and a fake bucket."""
from __future__ import annotations

import io
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts import backup_dashboard_db as b
from scripts import restore_test_dashboard_db as r


class FakeS3:
    def __init__(self, root: Path):
        self.root = root
        self.meta: dict[str, dict] = {}

    def _p(self, key):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        shutil.copyfile(filename, self._p(key))
        self.meta[key] = (ExtraArgs or {}).get("Metadata", {})

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self._p(Key).write_bytes(Body)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self._p(Key).read_bytes())}

    def download_file(self, bucket, key, filename):
        shutil.copyfile(self._p(key), filename)

    def delete_object(self, Bucket, Key):
        p = self.root / Key
        if p.exists():
            p.unlink()

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in self.root.rglob("*") if p.is_file())
        return {"Contents": [{"Key": k} for k in keys if k.startswith(Prefix)], "IsTruncated": False}


def _db(path: Path, rows: int = 50) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, sym TEXT)")
    conn.execute("CREATE TABLE signals_log (id INTEGER PRIMARY KEY, payload TEXT)")
    conn.executemany("INSERT INTO trades (sym) VALUES (?)", [(f"S{i}",) for i in range(rows)])
    conn.executemany("INSERT INTO signals_log (payload) VALUES (?)", [("x" * 200,) for _ in range(rows * 3)])
    conn.commit()
    conn.close()
    return path


NOW = datetime(2026, 9, 27, 15, 0, 0, tzinfo=b.IST)


def test_backup_uploads_consistent_copy_and_manifest(tmp_path):
    src = _db(tmp_path / "dashboard.db")
    s3 = FakeS3(tmp_path / "bucket")
    rep = b.run(src=src, upload=True, client=s3, bkt="x", now=NOW, tmp_root=str(tmp_path), retention_days=14)
    assert rep["ok"] and rep["quick_check"] == "ok"
    assert rep["table_counts"] == {"signals_log": 150, "trades": 50}
    assert b.list_backups(s3, "x") == ["dashboard-db/dashboard-20260927-150000.db.gz"]
    assert s3.meta[rep["key"]]["sha256"] == rep["sha256"]
    assert not any(p.name.startswith("dbbackup-") for p in tmp_path.iterdir())  # scratch cleaned


def test_restore_round_trip_matches_manifest_and_live(tmp_path):
    src = _db(tmp_path / "dashboard.db")
    s3 = FakeS3(tmp_path / "bucket")
    b.run(src=src, upload=True, client=s3, bkt="x", now=NOW, tmp_root=str(tmp_path))
    conn = sqlite3.connect(src)
    conn.execute("INSERT INTO trades (sym) VALUES ('NEW')")  # live keeps growing after the backup
    conn.commit()
    conn.close()
    res = r.restore_test(s3, "x", None, str(tmp_path), src)
    assert res["ok"] and res["sha256_ok"] and res["quick_check"] == "ok"
    assert res["manifest_mismatches"] == {}
    assert res["live_vs_backup"] == {"trades": 1} and res["live_shrunk"] == {}
    assert res["key_tables"] == {"trades": 50, "signals_log": 150}


def test_restore_detects_tampered_backup(tmp_path):
    src = _db(tmp_path / "dashboard.db")
    s3 = FakeS3(tmp_path / "bucket")
    rep = b.run(src=src, upload=True, client=s3, bkt="x", now=NOW, tmp_root=str(tmp_path))
    with open(tmp_path / "bucket" / rep["key"], "ab") as f:
        f.write(b"garbage")
    res = r.restore_test(s3, "x", None, str(tmp_path), None)
    assert res["sha256_ok"] is False and res["ok"] is False


def test_prune_keeps_retention_window_and_minimum(tmp_path):
    src = _db(tmp_path / "dashboard.db", rows=5)
    s3 = FakeS3(tmp_path / "bucket")
    for days_ago in (30, 20, 15, 10, 1):
        b.run(src=src, upload=True, client=s3, bkt="x", now=NOW - timedelta(days=days_ago), tmp_root=str(tmp_path),
              retention_days=365)
    rep = b.run(src=src, upload=True, client=s3, bkt="x", now=NOW, tmp_root=str(tmp_path), retention_days=14)
    kept = b.list_backups(s3, "x")
    assert len(rep["pruned"]) == 3 and len(kept) == 3
    assert kept[-1] == rep["key"]
    assert not (tmp_path / "bucket" / rep["pruned"][0].replace(".db.gz", ".manifest.json")).exists()


def test_refuses_without_scratch_space(tmp_path, monkeypatch):
    src = _db(tmp_path / "dashboard.db")
    monkeypatch.setattr(b.shutil, "disk_usage", lambda p: type("U", (), {"free": 10})())
    with pytest.raises(RuntimeError, match="not enough scratch space"):
        b.run(src=src, upload=False, now=NOW, tmp_root=str(tmp_path))


def test_failed_integrity_check_aborts_before_upload(tmp_path, monkeypatch):
    src = _db(tmp_path / "dashboard.db")
    s3 = FakeS3(tmp_path / "bucket")
    monkeypatch.setattr(b, "quick_check", lambda p: "*** in database main ***")
    with pytest.raises(RuntimeError, match="quick_check failed"):
        b.run(src=src, upload=True, client=s3, bkt="x", now=NOW, tmp_root=str(tmp_path))
    assert b.list_backups(s3, "x") == []


def test_stamp_parsing():
    assert b.stamp_of("dashboard-db/dashboard-20260927-023000.db.gz") == datetime(2026, 9, 27, 2, 30, tzinfo=b.IST)
    assert b.stamp_of("dashboard-db/other.txt") is None
