#!/usr/bin/env python3
"""
Read-only audit for Claude Code account-risk environment markers.

Checks:
- system timezone: Asia/Shanghai / Asia/Urumqi
- ANTHROPIC_BASE_URL in env and relevant config files
- China domains / China AI provider endpoints
- macOS Location Services disabled/enabled/unknown
- outbound public IP not in CN/HK/MO
- proxy and Claude/Anthropic residue in env/config files
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import platform
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


HIGH_RISK_TZ = {"Asia/Shanghai", "Asia/Urumqi"}
DISALLOWED_OUTBOUND_COUNTRIES = {"CN", "HK", "MO"}

OFFICIAL_ANTHROPIC_SUFFIXES = (
    "anthropic.com",
)

CHINA_TLDS = (
    ".cn",
    ".中国",
    ".公司",
    ".网络",
    ".香港",
)

CHINA_REGION_PATTERNS = (
    "cn-",
    "-cn",
    "china",
    "beijing",
    "shanghai",
    "guangzhou",
    "shenzhen",
    "hangzhou",
    "wulanchabu",
    "zhangjiakou",
    "qingdao",
)

CHINA_AI_PROVIDER_PATTERNS = (
    "deepseek",
    "bigmodel",
    "zhipu",
    "zhipuai",
    "moonshot",
    "kimi",
    "baichuan",
    "minimax",
    "siliconflow",
    "01.ai",
    "lingyiwanwu",
    "stepfun",
    "sensetime",
    "sensecore",
    "dashscope",
    "aliyuncs",
    "alibaba",
    "qwen",
    "tongyi",
    "volces",
    "volcengine",
    "doubao",
    "ark.cn-",
    "tencentcloud",
    "hunyuan",
    "baidubce",
    "qianfan",
    "ernie",
    "xfyun",
    "iflytek",
    "modelbest",
)

SECRET_KEY_PATTERNS = re.compile(
    r"(api[_-]?key|auth[_-]?token|token|secret|password|cookie|session)",
    re.IGNORECASE,
)

ENV_NAME_PATTERNS = re.compile(
    r"(ANTHROPIC|CLAUDE|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|NO_PROXY|http_proxy|https_proxy|all_proxy|no_proxy)"
)

TEXT_EXTENSIONS = {
    "",
    ".env",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".conf",
    ".config",
    ".rc",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".bashrc",
    ".bash_profile",
    ".fish",
    ".txt",
}


def run_command(args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return None
    output = (proc.stdout or "").strip()
    return output or None


def detect_timezone() -> dict[str, Any]:
    candidates: list[dict[str, str]] = []

    env_tz = os.environ.get("TZ")
    if env_tz:
        candidates.append({"source": "env:TZ", "value": env_tz})

    if platform.system() == "Darwin":
        output = run_command(["systemsetup", "-gettimezone"])
        if output and "administrator access" not in output.lower():
            value = output.split(":", 1)[-1].strip() if ":" in output else output
            candidates.append({"source": "systemsetup", "value": value})

    output = run_command(["timedatectl", "show", "-p", "Timezone", "--value"])
    if output:
        candidates.append({"source": "timedatectl", "value": output.splitlines()[0].strip()})

    localtime = Path("/etc/localtime")
    try:
        if localtime.exists():
            target = os.path.realpath(str(localtime))
            marker = "zoneinfo/"
            if marker in target:
                candidates.append({"source": "/etc/localtime", "value": target.split(marker, 1)[1]})
    except Exception:
        pass

    for tz_file in (Path("/etc/timezone"), Path("/var/db/timezone/zone")):
        try:
            if tz_file.exists() and tz_file.is_file():
                value = tz_file.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
                if value:
                    candidates.append({"source": str(tz_file), "value": value})
        except Exception:
            pass

    seen: set[tuple[str, str]] = set()
    unique = []
    for item in candidates:
        key = (item["source"], item["value"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    matched = [item for item in unique if item["value"] in HIGH_RISK_TZ]
    return {
        "status": "HIGH" if matched else "OK",
        "detected": unique,
        "high_risk_matches": matched,
    }


def read_plist(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        with path.open("rb") as fh:
            data = plistlib.load(fh)
        if isinstance(data, dict):
            return data, None
        return None, "plist root is not a dictionary"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def detect_location_services() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {
            "status": "OK",
            "state": "not_applicable",
            "details": [{"source": "platform", "state": platform.system()}],
        }

    details: list[dict[str, Any]] = []
    paths = [
        Path("/Library/Preferences/com.apple.locationd.plist"),
        Path("/var/db/locationd/Library/Preferences/com.apple.locationd.plist"),
    ]

    by_host_dir = Path("/var/db/locationd/Library/Preferences/ByHost")
    try:
        paths.extend(sorted(by_host_dir.glob("com.apple.locationd*.plist")))
    except Exception as exc:
        details.append({"source": str(by_host_dir), "state": "unreadable", "error": f"{type(exc).__name__}: {exc}"})

    seen: set[str] = set()
    for path in paths:
        path_key = str(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        try:
            exists = path.exists()
        except Exception as exc:
            details.append({"source": str(path), "state": "unreadable", "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not exists:
            details.append({"source": str(path), "state": "missing"})
            continue
        data, error = read_plist(path)
        if error:
            details.append({"source": str(path), "state": "unreadable", "error": error})
            continue
        if data is not None and "LocationServicesEnabled" in data:
            enabled = bool(data.get("LocationServicesEnabled"))
            details.append({"source": str(path), "state": "enabled" if enabled else "disabled", "value": enabled})
            return {
                "status": "WARN" if enabled else "OK",
                "state": "enabled" if enabled else "disabled",
                "details": details,
            }
        details.append({"source": str(path), "state": "readable_no_key"})

    # locationd can be running even when the user-facing switch is disabled.
    # Include it only as a diagnostic detail, not as proof that location is on.
    launchctl = run_command(["launchctl", "print", "system/com.apple.locationd"])
    if launchctl:
        state_match = re.search(r"^\s*state = (.+)$", launchctl, re.MULTILINE)
        details.append(
            {
                "source": "launchctl system/com.apple.locationd",
                "state": "daemon_" + (state_match.group(1).strip() if state_match else "present"),
            }
        )

    return {
        "status": "UNKNOWN",
        "state": "unknown",
        "details": details,
        "note": "Could not confirm Location Services are disabled with normal user permissions.",
    }


def redact_ip(ip: str) -> str:
    if not ip:
        return ""
    if ":" in ip:
        parts = ip.split(":")
        return ":".join(parts[:2] + ["***"] * max(1, len(parts) - 2))
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:2] + ["***", "***"])
    return "***"


def fetch_json(url: str, timeout: int = 5) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "claude-code-risk-audit/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(1024 * 128)
    data = json.loads(body.decode("utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError("response JSON root is not an object")
    return data


def normalize_country_code(value: Any) -> str:
    return str(value or "").strip().upper()


def detect_outbound_ip(skip: bool = False) -> dict[str, Any]:
    if skip:
        return {
            "status": "WARN",
            "state": "skipped",
            "details": [{"source": "argument", "state": "skipped"}],
            "note": "Outbound IP check was skipped.",
        }

    providers = [
        {
            "name": "ipapi.co",
            "url": "https://ipapi.co/json/",
            "ip_keys": ("ip",),
            "country_keys": ("country_code", "country"),
            "region_keys": ("region", "region_code"),
        },
        {
            "name": "ipinfo.io",
            "url": "https://ipinfo.io/json",
            "ip_keys": ("ip",),
            "country_keys": ("country",),
            "region_keys": ("region",),
        },
        {
            "name": "ip-api.com",
            "url": "http://ip-api.com/json/?fields=status,message,country,countryCode,regionName,query",
            "ip_keys": ("query",),
            "country_keys": ("countryCode",),
            "region_keys": ("regionName",),
        },
    ]

    details: list[dict[str, Any]] = []
    for provider in providers:
        try:
            data = fetch_json(provider["url"])
        except Exception as exc:
            details.append({"source": provider["name"], "state": "error", "error": f"{type(exc).__name__}: {exc}"})
            continue

        if provider["name"] == "ip-api.com" and data.get("status") == "fail":
            details.append({"source": provider["name"], "state": "error", "error": str(data.get("message") or "lookup failed")})
            continue

        ip = next((str(data.get(key) or "") for key in provider["ip_keys"] if data.get(key)), "")
        country = normalize_country_code(next((data.get(key) for key in provider["country_keys"] if data.get(key)), ""))
        region = str(next((data.get(key) for key in provider["region_keys"] if data.get(key)), "") or "")

        details.append(
            {
                "source": provider["name"],
                "state": "detected",
                "ip": redact_ip(ip),
                "country_code": country,
                "region": region,
            }
        )

        if not country:
            continue

        disallowed = country in DISALLOWED_OUTBOUND_COUNTRIES
        return {
            "status": "HIGH" if disallowed else "OK",
            "state": "disallowed_region" if disallowed else "allowed_region",
            "country_code": country,
            "ip": redact_ip(ip),
            "region": region,
            "details": details,
        }

    return {
        "status": "WARN",
        "state": "unknown",
        "details": details,
        "note": "Could not confirm outbound IP country/region.",
    }


def normalize_url(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    if not value:
        return value
    if "://" not in value:
        return "https://" + value
    return value


def hostname_from_value(value: str) -> str:
    parsed = urlparse(normalize_url(value))
    host = parsed.hostname or ""
    return host.lower().strip(".")


def classify_endpoint(value: str) -> dict[str, Any]:
    host = hostname_from_value(value)
    if not host:
        return {"status": "WARN", "host": "", "reasons": ["not a parseable URL/host"]}

    reasons: list[str] = []
    labels = host.split(".")
    suffix2 = ".".join(labels[-2:]) if len(labels) >= 2 else host

    if host == "api.anthropic.com" or any(host == s or host.endswith("." + s) for s in OFFICIAL_ANTHROPIC_SUFFIXES):
        return {"status": "OK", "host": host, "reasons": ["official Anthropic domain"]}

    if any(host.endswith(tld) for tld in CHINA_TLDS):
        reasons.append("China TLD")

    haystack = host.lower()
    if any(pattern in haystack for pattern in CHINA_AI_PROVIDER_PATTERNS):
        reasons.append("known China AI provider/lab endpoint")

    if any(pattern in haystack for pattern in CHINA_REGION_PATTERNS):
        reasons.append("China-region endpoint string")

    if reasons:
        return {"status": "HIGH", "host": host, "reasons": sorted(set(reasons))}

    return {"status": "WARN", "host": host, "reasons": [f"non-official Anthropic endpoint ({suffix2})"]}


def redact_value(key: str, value: str) -> str:
    if not value:
        return ""
    if SECRET_KEY_PATTERNS.search(key):
        return "***"

    if "://" in value:
        parsed = urlparse(value)
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, host = netloc.rsplit("@", 1)
            netloc = "***@" + host if userinfo else host
        return parsed._replace(netloc=netloc, path=parsed.path[:120], query="", fragment="").geturl()

    if len(value) > 160:
        return value[:80] + "...<truncated>..." + value[-40:]
    return value


def env_audit() -> dict[str, Any]:
    anthropic_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    base_url_result = classify_endpoint(anthropic_base_url) if anthropic_base_url else None

    relevant = []
    for key, value in sorted(os.environ.items()):
        if ENV_NAME_PATTERNS.search(key):
            relevant.append({"name": key, "value": redact_value(key, value)})

    status = "OK"
    if base_url_result and base_url_result["status"] == "HIGH":
        status = "HIGH"
    elif (
        base_url_result
        and base_url_result["status"] != "OK"
        or any("PROXY" in item["name"].upper() for item in relevant)
    ):
        status = "WARN"

    return {
        "status": status,
        "anthropic_base_url": {
            "present": anthropic_base_url is not None,
            "value": redact_value("ANTHROPIC_BASE_URL", anthropic_base_url or ""),
            "classification": base_url_result,
        },
        "relevant_variables": relevant,
    }


def candidate_files(cwd: Path) -> list[Path]:
    home = Path.home()
    paths = [
        home / ".zshenv",
        home / ".zprofile",
        home / ".zshrc",
        home / ".bash_profile",
        home / ".bashrc",
        home / ".profile",
        home / ".config" / "fish" / "config.fish",
        home / ".claude.json",
        home / ".claude" / "settings.json",
        home / ".claude" / "settings.local.json",
        home / ".config" / "claude" / "settings.json",
        home / ".config" / "claude-code" / "settings.json",
        cwd / ".claude" / "settings.json",
        cwd / ".claude" / "settings.local.json",
    ]

    for root in (
        home / ".claude",
        home / ".config" / "claude",
        home / ".config" / "claude-code",
        cwd / ".claude",
    ):
        if root.exists() and root.is_dir():
            try:
                for path in root.rglob("*"):
                    if path.is_file() and path.suffix in TEXT_EXTENSIONS:
                        paths.append(path)
            except Exception:
                pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = str(path.expanduser().resolve())
        except Exception:
            resolved = str(path.expanduser())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(Path(resolved))
    return unique


LINE_PATTERNS = re.compile(
    r"(ANTHROPIC_BASE_URL|ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|"
    r"HTTP_PROXY|HTTPS_PROXY|ALL_PROXY|http_proxy|https_proxy|all_proxy|"
    r"https?://[^\s'\",}]+(?:\.cn\b|cn-|beijing|shanghai|guangzhou|shenzhen|hangzhou|"
    + "|".join(re.escape(p) for p in CHINA_AI_PROVIDER_PATTERNS)
    + r"))",
    re.IGNORECASE,
)


def redact_line(line: str) -> str:
    line = re.sub(
        r"(?i)(ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|TOKEN|SECRET|PASSWORD|COOKIE)\s*[:=]\s*['\"]?[^'\"\s,}]+",
        lambda m: m.group(1) + "=***",
        line,
    )
    line = re.sub(r"(https?://)([^/\s:@]+):([^@\s/]+)@", r"\1***:***@", line)
    if len(line) > 240:
        return line[:200] + "...<truncated>"
    return line


def scan_config_files(cwd: Path, max_file_bytes: int = 1024 * 1024) -> dict[str, Any]:
    matches = []
    scanned = 0

    for path in candidate_files(cwd):
        if not path.exists() or not path.is_file():
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        scanned += 1
        for number, line in enumerate(text.splitlines(), 1):
            if not LINE_PATTERNS.search(line):
                continue
            endpoint_classification = None
            if "ANTHROPIC_BASE_URL" in line:
                value = extract_assignment_value(line)
                if value:
                    endpoint_classification = classify_endpoint(value)
            matches.append(
                {
                    "file": str(path),
                    "line": number,
                    "text": redact_line(line.strip()),
                    "endpoint_classification": endpoint_classification,
                }
            )

    has_high = any(
        item.get("endpoint_classification")
        and item["endpoint_classification"].get("status") == "HIGH"
        for item in matches
    )
    status = "HIGH" if has_high else ("WARN" if matches else "OK")
    return {"status": status, "scanned_files": scanned, "matches": matches}


def extract_assignment_value(line: str) -> str | None:
    # Handles: export ANTHROPIC_BASE_URL=..., ANTHROPIC_BASE_URL: "...", JSON fragments.
    match = re.search(r"ANTHROPIC_BASE_URL['\"]?\s*[:=]\s*['\"]?([^'\"\s,}]+)", line)
    if match:
        return match.group(1).strip()
    return None


def overall_status(parts: list[str]) -> str:
    if "HIGH" in parts:
        return "HIGH"
    if "WARN" in parts:
        return "WARN"
    if "UNKNOWN" in parts:
        return "UNKNOWN"
    return "OK"


def make_report(cwd: Path, skip_ip_check: bool = False) -> dict[str, Any]:
    timezone = detect_timezone()
    location_services = detect_location_services()
    outbound_ip = detect_outbound_ip(skip=skip_ip_check)
    env = env_audit()
    residue = scan_config_files(cwd)
    status = overall_status([timezone["status"], location_services["status"], outbound_ip["status"], env["status"], residue["status"]])
    return {
        "overall_status": status,
        "timezone": timezone,
        "location_services": location_services,
        "outbound_ip": outbound_ip,
        "environment": env,
        "residual_markers": residue,
    }


def print_text_report(report: dict[str, Any]) -> None:
    print(f"Overall: {report['overall_status']}")
    print()

    tz = report["timezone"]
    print(f"Timezone: {tz['status']}")
    if tz["detected"]:
        for item in tz["detected"]:
            marker = " HIGH" if item in tz["high_risk_matches"] else ""
            print(f"  - {item['source']}: {item['value']}{marker}")
    else:
        print("  - no timezone identifier detected")
    print()

    location = report["location_services"]
    print(f"Location Services: {location['status']}")
    print(f"  - state: {location['state']}")
    for item in location.get("details", []):
        extra = ""
        if "value" in item:
            extra += f"; value={item['value']}"
        if "error" in item:
            extra += f"; error={item['error']}"
        print(f"  - {item['source']}: {item['state']}{extra}")
    if location.get("note"):
        print(f"  - note: {location['note']}")
    print()

    outbound = report["outbound_ip"]
    print(f"Outbound IP: {outbound['status']}")
    print(f"  - state: {outbound['state']}")
    if outbound.get("country_code"):
        print(f"  - country_code: {outbound['country_code']}")
    if outbound.get("region"):
        print(f"  - region: {outbound['region']}")
    if outbound.get("ip"):
        print(f"  - ip: {outbound['ip']}")
    for item in outbound.get("details", []):
        extra = ""
        if "ip" in item:
            extra += f"; ip={item['ip']}"
        if "country_code" in item:
            extra += f"; country_code={item['country_code']}"
        if "region" in item and item["region"]:
            extra += f"; region={item['region']}"
        if "error" in item:
            extra += f"; error={item['error']}"
        print(f"  - {item['source']}: {item['state']}{extra}")
    if outbound.get("note"):
        print(f"  - note: {outbound['note']}")
    print()

    env = report["environment"]
    print(f"Environment: {env['status']}")
    base = env["anthropic_base_url"]
    if base["present"]:
        print(f"  - ANTHROPIC_BASE_URL={base['value']}")
        cls = base["classification"]
        if cls:
            reasons = ", ".join(cls.get("reasons", []))
            print(f"    host={cls.get('host') or '<none>'}; status={cls.get('status')}; reasons={reasons}")
    else:
        print("  - ANTHROPIC_BASE_URL is not set in current process")
    if env["relevant_variables"]:
        print("  - relevant variables:")
        for item in env["relevant_variables"]:
            print(f"    {item['name']}={item['value']}")
    print()

    residue = report["residual_markers"]
    print(f"Residual markers: {residue['status']}")
    print(f"  - scanned files: {residue['scanned_files']}")
    if residue["matches"]:
        for item in residue["matches"]:
            cls = item.get("endpoint_classification")
            suffix = ""
            if cls:
                suffix = f" [{cls.get('status')}: {', '.join(cls.get('reasons', []))}]"
            print(f"  - {item['file']}:{item['line']}: {item['text']}{suffix}")
    else:
        print("  - no relevant config residue found")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Claude Code environment risk markers.")
    parser.add_argument("--cwd", default=os.getcwd(), help="Project directory whose .claude/ config should be scanned.")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a text report.")
    parser.add_argument("--strict-exit", action="store_true", help="Return non-zero when WARN/HIGH markers are found.")
    parser.add_argument("--skip-ip-check", action="store_true", help="Skip outbound public IP geolocation lookup.")
    args = parser.parse_args()

    cwd = Path(args.cwd).expanduser().resolve()
    report = make_report(cwd, skip_ip_check=args.skip_ip_check)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text_report(report)

    if args.strict_exit and report["overall_status"] != "OK":
        return 2 if report["overall_status"] == "HIGH" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
