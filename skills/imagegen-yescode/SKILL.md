---
name: responses-imagegen
description: Generate PNG images through a Responses-style image generation endpoint such as https://co.yes.vg/v1/responses. Use when the user asks to generate an image with the custom Responses image proxy, gpt-image-1024x1024, co.yes.vg, or wants image generation using OPENAI_API_KEY instead of the built-in image_gen tool or the standard /v1/images/generations endpoint.
---

# Responses ImageGen

Generate images by POSTing to a Responses-compatible endpoint that exposes image generation through `model: "gpt-image-1024x1024"` and returns base64 image data in an SSE stream.

This skill is for the custom Responses image proxy path. Do not use the standard OpenAI Images API `/v1/images/generations` path for this skill.

The bundled script supports text-only generation and image-reference generation. Use `--ref` to attach one or more local image files as `input_image` content in the Responses payload.

## Quick Start

Use the bundled script:

```bash
python3 /Users/aaron/code/ai-skills/skills/responses-imagegen/scripts/generate_image.py \
  --prompt "一个奶油色拉布拉多在教室答初中物理题,写实风格" \
  --out output/imagegen/labrador-physics.png
```

With reference images:

```bash
python3 /Users/aaron/code/ai-skills/skills/responses-imagegen/scripts/generate_image.py \
  --prompt "参考照片中的人物坐在江景房客厅沙发上,写实照片" \
  --ref raw/selfPhoto/20201101.jpg \
  --ref raw/selfPhoto/20200928.jpeg \
  --out output/imagegen/riverview-portrait.png
```

The script reads `OPENAI_API_KEY` from the environment. It never prints the key.

Endpoint resolution:

1. Use `--endpoint` when explicitly provided.
2. Else use `OPENAI_IMAGE_RESPONSES_URL` when set.
3. Else use `OPENAI_BASE_URL` when set.
4. Else default to `https://co.yes.vg/v1/responses`.

If `OPENAI_BASE_URL` is a root such as `https://co.yes.vg/v1`, the script appends `/responses`. If it already ends in `/responses`, it uses it unchanged.

## Workflow

1. Make sure `OPENAI_API_KEY` is set in the shell that will run the command.
2. Create an output path under `output/imagegen/` unless the user requested a different destination.
3. Run `scripts/generate_image.py` with the user's image description as `--prompt`.
4. If the user provides reference images, pass each local image path with repeated `--ref` arguments.
5. Wait for the SSE stream to finish. Image generation can take several minutes.
6. Report the saved PNG path. Do not keep raw SSE files after successful generation.

## Options

- `--prompt`: Required image description.
- `--out`: Output PNG path. Defaults to `output/imagegen/response-image.png`.
- `--ref`: Optional local reference image path. Repeat to attach multiple images. The script base64-encodes each file as a data URL and sends it as `input_image`.
- `--model`: Defaults to `gpt-image-1024x1024`.
- `--endpoint`: Optional override for the Responses endpoint.
- `--raw-out`: Optional temporary path for the raw SSE response. The script deletes it after successful image generation unless `--keep-raw` is set.
- `--keep-raw`: Keep `--raw-out` after success. Use only for debugging.
- `--timeout`: Request timeout in seconds. Defaults to `600`.
- `--force`: Overwrite an existing output file.

## Notes

- The endpoint returns intermediate `partial_image_b64` events and a final `result` field. Prefer `result`; use partial data only if no final result exists.
- The observed response maps `gpt-image-1024x1024` to a Responses `image_generation` tool backed by `gpt-image-2`.
- Keep prompts in the user's requested language unless they ask for translation or prompt expansion.
- Do not expose API keys in commands or final answers. Use `$OPENAI_API_KEY`.
