#!/usr/bin/env python3
"""
AgriTrace Standalone Keep-Alive Robot
Can be run on any machine, VPS, or cron job to keep the Render deployment awake.
Render spins down after 15 minutes of inactivity; this script pings every 10 minutes by default.

Usage:
    # Continuous ping robot (default 600 seconds = 10 minutes)
    python scripts/render_keep_alive.py --url https://agri-trace-4x5p.onrender.com

    # One-time ping check
    python scripts/render_keep_alive.py --url https://agri-trace-4x5p.onrender.com --once

    # Custom interval (e.g. 5 minutes)
    python scripts/render_keep_alive.py --url https://agri-trace-4x5p.onrender.com --interval 300
"""

import argparse
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

def ping(url: str) -> bool:
    target = f"{url.rstrip('/')}/api/health"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    req = urllib.request.Request(
        target,
        headers={"User-Agent": "AgriTrace-KeepAlive-Robot/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            status = resp.getcode()
            print(f"[{now_str}] Ping to {target} -> SUCCESS (HTTP {status})")
            return status == 200
    except urllib.error.HTTPError as e:
        print(f"[{now_str}] Ping to {target} -> HTTP ERROR: {e.code} {e.reason}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[{now_str}] Ping to {target} -> FAILED: {e}", file=sys.stderr)
        return False

def main():
    parser = argparse.ArgumentParser(description="Render Keep-Alive Robot for AgriTrace")
    parser.add_argument("--url", default="https://agri-trace-4x5p.onrender.com", help="Render service base URL")
    parser.add_argument("--interval", type=int, default=600, help="Ping interval in seconds (default: 600 / 10 minutes)")
    parser.add_argument("--once", action="store_true", help="Perform a single ping and exit")
    args = parser.parse_args()

    print(f"Starting AgriTrace Keep-Alive Robot")
    print(f"Target URL: {args.url}")
    print(f"Ping Interval: {args.interval}s (Render sleeps after 900s)")

    if args.once:
        success = ping(args.url)
        sys.exit(0 if success else 1)

    while True:
        try:
            ping(args.url)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nKeep-Alive Robot stopped by user.")
            break

if __name__ == "__main__":
    main()

