"""
AgriTrace Automated Keep-Alive Robot
Prevents Render free tier instances from sleeping (Render spins down after 15 minutes of inactivity).
Pings the public health endpoint every 10 minutes (600 seconds) to ensure continuous responsiveness.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
import httpx

logger = logging.getLogger("agritrace.keep_alive")

# In-memory status for monitoring and diagnostics
keep_alive_stats = {
    "status": "stopped",
    "target_url": None,
    "last_ping_time": None,
    "last_status_code": None,
    "total_pings": 0,
    "failed_pings": 0,
    "last_error": None
}

def resolve_target_url() -> str:
    """
    Resolves the public external URL of the service.
    Render automatically exposes RENDER_EXTERNAL_URL.
    Can also be overridden by APP_URL, SITE_URL, or PING_URL.
    """
    from backend.config import settings
    url = (
        os.getenv("RENDER_EXTERNAL_URL")
        or os.getenv("APP_URL")
        or os.getenv("SITE_URL")
        or os.getenv("PING_URL")
        or getattr(settings, "RENDER_EXTERNAL_URL", "")
        or "https://agritrace.onrender.com"
    ).strip()
    return url.rstrip("/")

async def ping_service(target_url: str) -> bool:
    """
    Sends an HTTP GET request to target_url/api/health.
    """
    health_url = f"{target_url}/api/health"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            resp = await client.get(health_url)
            keep_alive_stats["last_ping_time"] = datetime.now(timezone.utc).isoformat()
            keep_alive_stats["last_status_code"] = resp.status_code
            keep_alive_stats["total_pings"] += 1
            if resp.status_code == 200:
                logger.info(f"[Keep-Alive Robot] Successfully pinged {health_url} (status: {resp.status_code})")
                return True
            else:
                logger.warning(f"[Keep-Alive Robot] Ping returned unexpected status {resp.status_code} for {health_url}")
                return False
    except Exception as exc:
        keep_alive_stats["failed_pings"] += 1
        keep_alive_stats["last_error"] = str(exc)
        logger.warning(f"[Keep-Alive Robot] Ping failed for {health_url}: {exc}")
        return False

async def keep_alive_worker():
    """
    Continuous background worker loop that pings the service every interval to keep Render awake.
    """
    from backend.config import settings
    interval = getattr(settings, "KEEP_ALIVE_INTERVAL_SECONDS", 600)
    target_url = resolve_target_url()
    
    keep_alive_stats["status"] = "running"
    keep_alive_stats["target_url"] = target_url
    logger.info(f"[Keep-Alive Robot] Started. Target: {target_url}/api/health, Interval: {interval}s")

    # Initial delay to give the server time to fully start and bind ports
    try:
        await asyncio.sleep(20)
    except asyncio.CancelledError:
        keep_alive_stats["status"] = "stopped"
        return

    while True:
        try:
            target_url = resolve_target_url()
            keep_alive_stats["target_url"] = target_url
            
            # Avoid pinging localhost in loop if not desired, but ping if public URL is configured
            if target_url and not target_url.startswith("http://localhost") and not target_url.startswith("http://127.0.0.1"):
                await ping_service(target_url)
        except asyncio.CancelledError:
            logger.info("[Keep-Alive Robot] Worker cancelled, stopping.")
            keep_alive_stats["status"] = "stopped"
            break
        except Exception as exc:
            logger.error(f"[Keep-Alive Robot] Unexpected error in worker loop: {exc}")
        
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            keep_alive_stats["status"] = "stopped"
            break

