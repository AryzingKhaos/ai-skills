---
name: claude-code-risk-audit
description: "Audit the current machine for Claude Code account-risk environment markers. Use when the user asks whether the current Claude Code/Anthropic environment could cause account suspension, ban risk, region-risk, China timezone risk, China/Hong Kong/Macau outbound IP risk, ANTHROPIC_BASE_URL risk, Chinese AI lab endpoint risk, proxy residue, macOS Location Services risk, or Claude/Anthropic configuration residue."
---

# Claude Code Risk Audit

Audit the local environment for risk markers that may make Claude Code usage look inconsistent with an official Anthropic setup. Treat results as a defensive self-audit, not a guarantee about Anthropic enforcement or a guide to bypass restrictions.

## Quick Start

Run the bundled script from the skill directory:

```bash
python3 scripts/audit_claude_code_risk.py --cwd "$PWD"
```

If the user asks for machine-readable output:

```bash
python3 scripts/audit_claude_code_risk.py --cwd "$PWD" --json
```

The script is read-only. It does not edit environment variables, shell profiles, Claude settings, caches, or credentials.

## What To Check

Always cover these five categories:

1. System timezone
   - Flag `Asia/Shanghai` and `Asia/Urumqi` as high risk.
   - Report the detected timezone source when available.

2. `ANTHROPIC_BASE_URL`
   - Check the live process environment.
   - Check common shell startup files.
   - Check Claude-related config files under `~/.claude`, `~/.config/claude`, `~/.config/claude-code`, and the current project's `.claude/`.
   - Flag values pointing to China TLDs, China-region cloud endpoints, or known China AI labs/providers.
   - Flag non-official Anthropic-compatible endpoints as warning-level even if they are not clearly China-based.

3. Residual markers
   - Report Claude/Anthropic-related environment variables without printing secret values.
   - Report proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, lowercase variants) without printing full credentials.
   - Search relevant config files for `ANTHROPIC_BASE_URL`, China AI provider domains, China-region endpoint strings, and Claude/Anthropic env assignments.

4. Location Services
   - On macOS, check whether system Location Services can be confirmed disabled.
   - Report `OK` only when Location Services is explicitly detected as disabled.
   - Report `WARN` when Location Services is explicitly detected as enabled.
   - Report `UNKNOWN` when normal user permissions cannot confirm the state.
   - On non-macOS systems, report not applicable.

5. Outbound IP region
   - Query the current public outbound IP with a no-key geolocation endpoint.
   - Report `HIGH` if the detected country/region code is `CN`, `HK`, or `MO`.
   - Report `OK` only if the detected country/region code is not `CN`, `HK`, or `MO`.
   - Report `WARN` if the IP region cannot be confirmed because network access or geolocation lookup fails.
   - Redact the public IP in normal text output; JSON output may include the redacted IP only.

## Output Guidance

Give the user a concise risk summary:

- `HIGH`: timezone is `Asia/Shanghai` or `Asia/Urumqi`, outbound IP is in `CN`/`HK`/`MO`, or `ANTHROPIC_BASE_URL` points to a China domain / China AI provider.
- `WARN`: `ANTHROPIC_BASE_URL` points to a non-official endpoint, proxy variables are present, config residue references Claude/Anthropic routing, or Location Services is enabled.
- `UNKNOWN`: a check such as Location Services cannot be confirmed with normal user permissions.
- `OK`: no high-risk or warning markers found by this audit.

Include file paths and variable names, but never print full API keys, auth tokens, cookies, or proxy passwords. Redact secrets as `***`.

## Boundaries

- Do not claim this can prove an account is safe from enforcement.
- Do not recommend evasion, identity spoofing, or bypassing regional restrictions.
- If the user asks what to do next, recommend using official Anthropic endpoints, a supported region, and removing stale unofficial endpoint/proxy settings through normal shell or app configuration.
