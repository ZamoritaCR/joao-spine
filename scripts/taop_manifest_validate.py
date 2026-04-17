#!/usr/bin/env python3
"""
TAOP manifest validator — cron-run health probe.

Reads .taop-manifest.yaml, probes every active product's live_port
and tunnel_hostname, writes summary to /tmp/taop-manifest-status.json,
emits single-line stdout summary, exits 0 if all healthy else 1.

Cron line:
  */5 * * * * /usr/bin/flock -n /tmp/taop-manifest-validate.lock \\
      /home/zamoritacr/joao-spine/scripts/taop_manifest_validate.py \\
      >> /home/zamoritacr/logs/taop-manifest.log 2>&1

Ref: gap-closure-20260416 / JOAO_TRUE_REAL_GAP.md section 10
"""
from __future__ import annotations
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. pip install --user pyyaml", file=sys.stderr)
    sys.exit(2)

# Manifest lives next to the script; also check canonical ~/.taop-manifest.yaml
_SCRIPT_DIR = Path(__file__).resolve().parent
_MANIFEST_CANDIDATES = [
    _SCRIPT_DIR.parent / ".taop-manifest.yaml",
    Path.home() / ".taop-manifest.yaml",
]
OUTPUT_PATH = Path("/tmp/taop-manifest-status.json")
HTTP_TIMEOUT_S = 5


def find_manifest() -> Path | None:
    for p in _MANIFEST_CANDIDATES:
        if p.exists():
            return p
    return None


def port_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
            return True
    except (OSError, ValueError):
        return False


def curl_status(url: str) -> tuple[int, float]:
    t0 = time.monotonic()
    try:
        out = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
             "-m", str(HTTP_TIMEOUT_S), url],
            capture_output=True, text=True, timeout=HTTP_TIMEOUT_S + 2,
        )
        code = int(out.stdout.strip() or "0")
    except Exception:
        code = 0
    duration_ms = (time.monotonic() - t0) * 1000
    return code, duration_ms


def main() -> int:
    manifest_path = find_manifest()
    if manifest_path is None:
        print("ERROR: manifest not found. Tried: " +
              ", ".join(str(p) for p in _MANIFEST_CANDIDATES), file=sys.stderr)
        return 2

    manifest = yaml.safe_load(manifest_path.read_text())
    products = manifest.get("products", []) or []

    results: list[dict] = []
    any_failed = False

    for p in products:
        pid = p.get("id", "?")
        status_decl = p.get("status", "active")
        if status_decl in ("retired", "candidate_for_retirement"):
            continue

        rec: dict = {"id": pid, "declared_status": status_decl, "checks": {}}

        port = p.get("live_port")
        if port:
            listening = port_listening(port)
            rec["checks"]["port"] = {"port": port, "listening": listening}
            if not listening and status_decl == "active":
                rec["issue"] = f"port {port} not listening"
                any_failed = True

        hp = p.get("health_probe")
        if hp:
            parts = hp.split(None, 1)
            url = parts[1].strip() if len(parts) == 2 else hp.strip()
            code, dur = curl_status(url)
            rec["checks"]["local_health"] = {
                "url": url, "http_code": code, "duration_ms": round(dur, 1),
            }
            expected = p.get("expected_status", 200)
            if code != expected and status_decl == "active":
                rec.setdefault("issue", f"local health returned {code} (want {expected})")
                any_failed = True

        tunnel = p.get("tunnel_hostname")
        if tunnel:
            url = f"https://{tunnel}/"
            code, dur = curl_status(url)
            rec["checks"]["tunnel"] = {
                "url": url, "http_code": code, "duration_ms": round(dur, 1),
            }
            if (code == 0 or code >= 500) and status_decl == "active":
                rec.setdefault("issue", f"tunnel returned {code}")
                any_failed = True

        results.append(rec)

    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "manifest": str(manifest_path),
        "total_checked": len(results),
        "issues_count": sum(1 for r in results if "issue" in r),
        "any_failed": any_failed,
        "results": results,
    }

    OUTPUT_PATH.write_text(json.dumps(summary, indent=2))

    bad = [r["id"] for r in results if "issue" in r]
    ts = summary["timestamp"]
    if bad:
        print(f"FAIL {ts} issues={len(bad)} products={','.join(bad)}")
    else:
        print(f"OK   {ts} products_checked={len(results)}")

    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
