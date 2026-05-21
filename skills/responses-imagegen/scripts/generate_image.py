#!/usr/bin/env python3
"""Generate an image through a Responses-style SSE endpoint.

The endpoint is expected to accept a payload like:
{
  "model": "gpt-image-1024x1024",
  "input": [{"type": "message", "role": "user", "content": [...]}],
  "stream": true,
  "store": false
}

It should return base64 PNG data in either a final "result" field or an
intermediate "partial_image_b64" field.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_ENDPOINT = "https://co.yes.vg/v1/responses"
DEFAULT_MODEL = "gpt-image-1024x1024"
DEFAULT_OUT = "output/imagegen/response-image.png"


def die(message: str, code: int = 1) -> None:
    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(code)


def resolve_endpoint(raw: Optional[str]) -> str:
    endpoint = (
        raw
        or os.getenv("OPENAI_IMAGE_RESPONSES_URL")
        or os.getenv("OPENAI_BASE_URL")
        or DEFAULT_ENDPOINT
    ).rstrip("/")

    if endpoint.endswith("/responses"):
        return endpoint
    if endpoint.endswith("/v1"):
        return endpoint + "/responses"
    return endpoint + "/v1/responses"


def encode_image(path: Path) -> str:
    if not path.exists():
        die(f"Reference image does not exist: {path}")
    if not path.is_file():
        die(f"Reference image is not a file: {path}")

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_payload(prompt: str, model: str, refs: list[Path]) -> bytes:
    content = [{"type": "input_text", "text": prompt}]
    for ref in refs:
        content.append({"type": "input_image", "image_url": encode_image(ref)})

    payload = {
        "model": model,
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": content,
            }
        ],
        "stream": True,
        "store": False,
    }
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def image_fields(obj: Any) -> Iterable[Tuple[str, str]]:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {"result", "b64_json", "image_b64", "partial_image_b64"} and isinstance(
                value, str
            ):
                yield key, value
            yield from image_fields(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from image_fields(item)


def looks_like_image_b64(value: str) -> bool:
    return value.startswith(("iVBOR", "/9j/", "UklGR")) or len(value) > 1000


def decode_sse_response(response: Any, raw_out: Optional[Path]) -> Tuple[str, str, bytes]:
    last_partial: Optional[str] = None
    final_result: Optional[str] = None
    final_field = ""
    raw_file = None

    if raw_out is not None:
        raw_out.parent.mkdir(parents=True, exist_ok=True)
        raw_file = raw_out.open("wb")

    try:
        for raw_line in response:
            if raw_file is not None:
                raw_file.write(raw_line)

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data: "):
                continue

            data = line[6:]
            if data == "[DONE]":
                continue

            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            if event_type:
                print(f"event: {event_type}", file=sys.stderr)

            for field, value in image_fields(event):
                if not looks_like_image_b64(value):
                    continue
                if field == "partial_image_b64":
                    last_partial = value
                else:
                    final_result = value
                    final_field = field
    finally:
        if raw_file is not None:
            raw_file.close()

    selected = final_result or last_partial
    selected_field = final_field or "partial_image_b64"
    if not selected:
        die("No image base64 found in the SSE response.")

    try:
        return selected_field, selected, base64.b64decode(selected)
    except Exception as exc:  # noqa: BLE001
        die(f"Failed to decode image base64 from field {selected_field}: {exc}")


def generate(args: argparse.Namespace) -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        die("OPENAI_API_KEY is not set.")

    out = Path(args.out)
    if out.exists() and not args.force:
        die(f"Output already exists: {out} (use --force to overwrite).")

    refs = [Path(ref) for ref in args.ref]
    endpoint = resolve_endpoint(args.endpoint)
    body = build_payload(args.prompt, args.model, refs)
    request = Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            # The tested proxy is behind Cloudflare and rejects Python's default urllib signature.
            "User-Agent": "curl/8.7.1",
            "Cache-Control": "no-cache",
        },
    )

    print(f"Calling Responses image endpoint: {endpoint}", file=sys.stderr)
    print(f"Model: {args.model}", file=sys.stderr)
    if refs:
        print(f"Reference images: {len(refs)}", file=sys.stderr)
    started = time.time()

    try:
        with urlopen(request, timeout=args.timeout) as response:
            field, _b64, raw = decode_sse_response(
                response, Path(args.raw_out) if args.raw_out else None
            )
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        die(f"HTTP {exc.code}: {detail}")
    except URLError as exc:
        die(f"Connection failed: {exc.reason}")
    except TimeoutError:
        die(f"Request timed out after {args.timeout}s.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    if args.raw_out and not args.keep_raw:
        raw_path = Path(args.raw_out)
        if raw_path.exists():
            raw_path.unlink()
    elapsed = time.time() - started
    print(f"Wrote {out} ({len(raw)} bytes, from {field}) in {elapsed:.1f}s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a PNG image through /v1/responses.")
    parser.add_argument("--prompt", required=True, help="Image description.")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Output PNG path. Default: {DEFAULT_OUT}")
    parser.add_argument(
        "--ref",
        action="append",
        default=[],
        help="Reference image path. Repeat this option to send multiple images.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model. Default: {DEFAULT_MODEL}")
    parser.add_argument("--endpoint", help="Responses endpoint override.")
    parser.add_argument("--raw-out", help="Optional temporary file for the raw SSE stream.")
    parser.add_argument("--keep-raw", action="store_true", help="Keep --raw-out after success.")
    parser.add_argument("--timeout", type=int, default=600, help="Request timeout in seconds.")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file.")
    args = parser.parse_args()

    generate(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
