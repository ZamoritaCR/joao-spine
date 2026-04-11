"""
Cloudflared Tunnel Adapter -- status and health for JOAO tunnels.

Reads:
- Process status (pgrep)
- Config file hostnames
- Tunnel connection info
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Known config locations
_CONFIG_PATHS = [
    Path("/etc/cloudflared/config.yml"),
    Path.home() / ".cloudflared" / "config.yml",
]


def status() -> dict:
    """Get cloudflared tunnel status.

    Returns: tunnel_name, hostnames, process info, connection status.
    """
    result = {
        "tunnel_name": "unknown",
        "hostnames": [],
        "hostnames_count": 0,
        "processes": [],
        "process_alive": False,
        "config_path": None,
    }

    # Find running cloudflared processes
    try:
        out = subprocess.run(
            ["pgrep", "-a", "cloudflared"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            procs = []
            for line in out.stdout.strip().split("\n"):
                parts = line.split(None, 1)
                if len(parts) >= 2:
                    procs.append({"pid": parts[0], "command": parts[1]})
            result["processes"] = procs
            result["process_alive"] = len(procs) > 0
    except Exception as e:
        logger.warning("Failed to check cloudflared processes: %s", e)

    # Read config for hostnames
    for config_path in _CONFIG_PATHS:
        if config_path.exists():
            result["config_path"] = str(config_path)
            try:
                import yaml
                config = yaml.safe_load(config_path.read_text())
                if config and "tunnel" in config:
                    result["tunnel_name"] = config["tunnel"]
                ingress = config.get("ingress", []) if config else []
                hostnames = []
                for rule in ingress:
                    if isinstance(rule, dict) and "hostname" in rule:
                        hostnames.append({
                            "hostname": rule["hostname"],
                            "service": rule.get("service", "unknown"),
                        })
                result["hostnames"] = hostnames
                result["hostnames_count"] = len(hostnames)
            except ImportError:
                # No pyyaml -- parse manually
                text = config_path.read_text()
                hostnames = []
                for line in text.split("\n"):
                    line = line.strip()
                    if line.startswith("hostname:"):
                        hostnames.append({"hostname": line.split(":", 1)[1].strip()})
                    elif line.startswith("tunnel:"):
                        result["tunnel_name"] = line.split(":", 1)[1].strip()
                result["hostnames"] = hostnames
                result["hostnames_count"] = len(hostnames)
            except Exception as e:
                logger.warning("Failed to parse cloudflared config: %s", e)
            break

    return result
