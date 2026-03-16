#!/usr/bin/env python3
"""
Cloud deployment audit script for Railway-hosted trading platform.

Usage:
  BACKEND_URL=https://your-api.up.railway.app REDIS_URL=redis://... python scripts/audit_cloud_deployment.py
  python scripts/audit_cloud_deployment.py --backend https://xxx.up.railway.app [--redis redis://...]

Checks:
  - Website/API reachability and JSON responses
  - Redis keys: engine_lock, engine_heartbeat, engine_started_at, engine_version, engine_last_cycle
  - Computes heartbeat and last_cycle ages; reports engine health
"""

import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    print("Install requests: pip install requests")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Audit cloud deployment (API + optional Redis)")
    parser.add_argument("--backend", default=os.getenv("BACKEND_URL"), help="Dashboard API base URL")
    parser.add_argument("--redis", default=os.getenv("REDIS_URL"), help="Redis URL for key checks")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout seconds")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON report")
    args = parser.parse_args()

    if not args.backend:
        print("Error: Set BACKEND_URL or pass --backend")
        sys.exit(1)

    base = args.backend.rstrip("/")
    report = {
        "website_status": None,
        "api_endpoints": {},
        "redis_keys": {},
        "redis_ages": {},
        "engine_health": None,
        "issues": [],
        "verdict": None,
    }

    # ─── CHECK 1 — Website / API ─────────────────────────────────────────────
    endpoints = [
        ("/health", "GET /health"),
        ("/api/system/health", "GET /api/system/health"),
        ("/api/snapshot", "GET /api/snapshot"),
        ("/api/agents/oi-intelligence", "GET /api/agents/oi-intelligence"),
    ]
    for path, label in endpoints:
        url = base + path
        try:
            r = requests.get(url, timeout=args.timeout)
            report["api_endpoints"][label] = {
                "status_code": r.status_code,
                "ok": r.status_code == 200,
                "content_type": r.headers.get("Content-Type", ""),
            }
            if r.status_code == 200:
                try:
                    _ = r.json()
                    report["api_endpoints"][label]["valid_json"] = True
                except Exception:
                    report["api_endpoints"][label]["valid_json"] = False
                    report["issues"].append(f"{label}: response is not valid JSON")
            else:
                report["issues"].append(f"{label}: HTTP {r.status_code}")
        except requests.exceptions.RequestException as e:
            report["api_endpoints"][label] = {"ok": False, "error": str(e)}
            report["issues"].append(f"{label}: {e}")

    health_ok = report["api_endpoints"].get("GET /health", {}).get("ok")
    report["website_status"] = "live" if health_ok else "not_live"

    # ─── CHECK 3 & 4 — Redis keys (if REDIS_URL set) ─────────────────────────
    if args.redis:
        try:
            import redis
            rclient = redis.from_url(args.redis, decode_responses=True)
            rclient.ping()
        except Exception as e:
            err_str = str(e)
            report["issues"].append(f"Redis connection: {err_str}")
            report["redis_keys"] = {"error": err_str}
            if "railway.internal" in args.redis or "getaddrinfo failed" in err_str:
                report["issues"].append(
                    "REDIS_URL uses Railway internal hostname - not reachable from your laptop. "
                    "Backend and engine on Railway can still use Redis. Use a public Redis URL to audit from PC."
                )
        else:
            keys = [
                "engine_lock",
                "engine_heartbeat",
                "engine_started_at",
                "engine_version",
                "engine_last_cycle",
            ]
            now = time.time()
            for key in keys:
                try:
                    val = rclient.get(key)
                    ttl = rclient.ttl(key) if val is not None else None
                    report["redis_keys"][key] = {"value": val, "ttl_sec": ttl if ttl >= 0 else None}
                except Exception as e:
                    report["redis_keys"][key] = {"error": str(e)}

            # Ages
            hb_raw = rclient.get("engine_heartbeat")
            lc_raw = rclient.get("engine_last_cycle")
            try:
                hb_ts = float(hb_raw) if hb_raw else None
                lc_ts = float(lc_raw) if lc_raw else None
                report["redis_ages"] = {
                    "engine_heartbeat_age_sec": round(now - hb_ts, 2) if hb_ts else None,
                    "engine_last_cycle_age_sec": round(now - lc_ts, 2) if lc_ts else None,
                }
            except (TypeError, ValueError):
                report["redis_ages"] = {"engine_heartbeat_age_sec": None, "engine_last_cycle_age_sec": None}

            # Engine health interpretation
            hb_age = report["redis_ages"].get("engine_heartbeat_age_sec")
            lc_age = report["redis_ages"].get("engine_last_cycle_age_sec")
            if hb_age is None and lc_age is None:
                report["engine_health"] = "offline"
                report["issues"].append("No heartbeat or last_cycle — engine likely not running")
            elif hb_age is not None and hb_age > 120:
                report["engine_health"] = "offline"
                report["issues"].append(f"Heartbeat age {hb_age}s > 120s — engine offline")
            elif hb_age is not None and lc_age is not None and lc_age > 120:
                report["engine_health"] = "alive_but_stuck"
                report["issues"].append(f"Last cycle age {lc_age}s > 120s (heartbeat fresh) — engine may be stuck")
            elif hb_age is not None and hb_age <= 60 and (lc_age is None or lc_age <= 60):
                report["engine_health"] = "running"
            else:
                report["engine_health"] = "stale"
    else:
        # Derive engine health from /api/system/health if available
        try:
            h = report["api_endpoints"].get("GET /api/system/health", {})
            if h.get("ok"):
                # We didn't fetch body; could do a second request for health body
                report["engine_health"] = "from_api_only"
        except Exception:
            pass

    # ─── Verdict ─────────────────────────────────────────────────────────────
    # Criteria: API 200, website live, engine_heartbeat_age_sec < 60, engine_last_cycle_age_sec < 60
    apis_ok = all(
        report["api_endpoints"].get(label, {}).get("ok")
        for label in ["GET /health", "GET /api/system/health", "GET /api/snapshot", "GET /api/agents/oi-intelligence"]
    )
    hb_age = report["redis_ages"].get("engine_heartbeat_age_sec")
    lc_age = report["redis_ages"].get("engine_last_cycle_age_sec")
    heartbeat_ok = hb_age is not None and hb_age < 60
    cycle_ok = lc_age is not None and lc_age < 60

    redis_unreachable = args.redis and "error" in report.get("redis_keys", {})
    if report["website_status"] != "live" or not apis_ok:
        report["verdict"] = "SYSTEM_DEPENDS_ON_LOCAL_MACHINE"
    elif redis_unreachable and apis_ok:
        report["verdict"] = "INCONCLUSIVE"
        report["issues"].append("API is live but Redis not reachable from this machine (e.g. internal URL). Engine on Railway may still be running; check Railway dashboard.")
    elif args.redis and (hb_age is None or lc_age is None):
        report["verdict"] = "SYSTEM_DEPENDS_ON_LOCAL_MACHINE"
    elif args.redis and (not heartbeat_ok or not cycle_ok):
        report["verdict"] = "SYSTEM_DEPENDS_ON_LOCAL_MACHINE"
    else:
        # No Redis URL: only API + website; with Redis: heartbeat and cycle both < 60
        if not args.redis:
            report["verdict"] = "INCONCLUSIVE"
            report["issues"].append("REDIS_URL not set — cannot verify engine heartbeat/cycle; set REDIS_URL for full verdict")
        else:
            report["verdict"] = "SYSTEM_FULLY_CLOUD_HOSTED"

    # ─── Output ──────────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps(report, indent=2))
        return

    # Human-readable
    print("=" * 60)
    print("CLOUD DEPLOYMENT AUDIT REPORT")
    print("=" * 60)
    print(f"Backend URL: {base}")
    print(f"Website status: {report['website_status']}")
    print()
    print("API endpoints:")
    for label, res in report["api_endpoints"].items():
        ok = res.get("ok", False)
        code = res.get("status_code", "—")
        valid = res.get("valid_json", "—")
        print(f"  {label}: {'OK' if ok else 'FAIL'} (HTTP {code}, JSON={valid})")
    if report["redis_keys"]:
        print()
        print("Redis keys:")
        for k, v in report["redis_keys"].items():
            if isinstance(v, dict) and "error" in v:
                print(f"  {k}: ERROR {v['error']}")
            else:
                val = v.get("value") if isinstance(v, dict) else v
                ttl = v.get("ttl_sec") if isinstance(v, dict) else None
                val_str = str(val)[:50] + "..." if val and len(str(val)) > 50 else val
                print(f"  {k}: value={val_str} ttl_sec={ttl}")
        if report["redis_ages"]:
            print()
            print("Redis ages (seconds):")
            for k, v in report["redis_ages"].items():
                print(f"  {k}: {v}")
    print()
    print(f"Engine health: {report.get('engine_health', '—')}")
    if report["issues"]:
        print()
        print("Issues:")
        for i in report["issues"]:
            print(f"  - {i}")
    print()
    if report["verdict"] == "SYSTEM_FULLY_CLOUD_HOSTED":
        print("OK SYSTEM FULLY CLOUD HOSTED - You can turn off your laptop safely.")
    elif report["verdict"] == "SYSTEM_DEPENDS_ON_LOCAL_MACHINE":
        print("FAIL SYSTEM DEPENDS ON LOCAL MACHINE - Laptop must stay on.")
    elif report["verdict"] == "INCONCLUSIVE":
        print("VERDICT: INCONCLUSIVE (API live; Redis not reachable from this PC).")
        print("  If the engine runs on Railway, system is likely cloud-hosted. Check Railway dashboard.")
    else:
        print("VERDICT:", report["verdict"])
        print("  -> Set REDIS_URL and re-run for full verdict, or resolve issues above.")
    print("=" * 60)

    sys.exit(0 if report["verdict"] == "SYSTEM_FULLY_CLOUD_HOSTED" else 1)


if __name__ == "__main__":
    main()
