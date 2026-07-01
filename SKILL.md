---
name: image-gen
description: Use this skill when a user asks Codex to generate images from text, edit or modify an existing image, or create image variations / one-image-to-many-images using the StudyHard token-api OpenAI-compatible image gateway. It submits asynchronous image tasks to image generation, edit, and variation endpoints, waits by default with foreground polling, records task state locally, prints task progress, and returns generated image URLs or Markdown image previews.
---

# StudyHard Image Gen

Use the bundled Python script for all image gateway calls. Do not hand-roll curl unless the script is unavailable.

## Configuration

Use `https://api.studyhard.help` as the default image gateway base URL.

Read image gateway API key configuration from the standard Codex config location:

- `CODEX_HOME/config.toml`, or `~/.codex/config.toml` when `CODEX_HOME` is unset.
- `CODEX_HOME/auth.json`, or `~/.codex/auth.json` when `CODEX_HOME` is unset.

Use the active `model_provider` from `config.toml` only for the bearer token, falling back to `auth.json` key `OPENAI_API_KEY`. Do not use the Codex provider `base_url`, and do not use the Codex top-level `model` as the image model. The default image model is `gpt-image-2`.

Environment variables are optional overrides only:

- `STUDYHARD_IMAGE_BASE_URL`: override the default base URL `https://api.studyhard.help` for debugging or staging.
- `STUDYHARD_IMAGE_API_KEY`: override Codex provider/auth API key.
- `STUDYHARD_IMAGE_MODEL`: override the default image model `gpt-image-2`.
- `STUDYHARD_IMAGE_OUT_DIR`: directory for local task state files. Defaults to the system temporary directory under `studyhard-images`.

If required config is missing, explain that Codex config must contain an API key, or use the `STUDYHARD_IMAGE_API_KEY` environment override.

## Intent Routing

- Text-to-image, draw, create, generate from prompt: run `submit-generation`.
- Edit, modify, replace, remove, or transform an existing image: run `submit-edit` with `--image` and optional `--mask`.
- Variation, one image to many images, make similar images, or image-to-image variants: run `submit-variation` with `--image`.
- User asks whether an image is ready, asks for a result, or gives a task id: run `status`.

## Parameter Extraction

Extract explicit user requirements into CLI arguments instead of burying them in the prompt:

- Count: "one/two/four images", "生成 3 张" -> `--n <count>`. Use generation default `1` and variation default `4` when unspecified.
- Size/aspect: map explicit resolution plus common ratio to `--size` instead of passing `aspect_ratio`. `1k`: `1:1=1024x1024`, `3:4=768x1024`, `4:3=1024x768`, `16:9=1024x576`, `9:16=576x1024`. `2k`: `1:1=2048x2048`, `3:4=1536x2048`, `4:3=2048x1536`, `16:9=2048x1152`, `9:16=1152x2048`. `4k`: `1:1=2880x2880`, `3:4=2448x3264`, `4:3=3264x2448`, `16:9=3840x2160`, `9:16=2160x3840`. If the user says only `1k`, `2k`, or `4k` without ratio, assume `1:1`. If no resolution is specified, map square or `1:1` to `1024x1024`; landscape/wide/horizontal, `3:2`, `9:6`, or `1.5:1` to `1536x1024`; portrait/vertical, `2:3`, `6:9`, or `1:1.5` to `1024x1536`. Preserve an explicit size such as `1024x1024`, `2048x2048`, `3840x2160`, or `auto`.
- Quality: "高清", "high quality", "精细" -> `--quality high`; "快速", "低成本", "草图" -> `--quality low`; "standard/hd" -> pass the explicit value.
- Background: "透明背景", "transparent background", "抠图/无背景" -> `--background transparent`; "不透明背景" -> `--background opaque`.
- Output format: "PNG/JPEG/WebP" -> generation `--output-format png|jpeg|webp` when requesting generated image encoding. For OpenAI-compatible URL responses, keep `--response-format url` unless the user explicitly requests base64 JSON.
- User field: only pass `--user` when the user explicitly provides an end-user identifier.
- Model: use `gpt-image-2` unless the user names another available model or the gateway requires one for a specific operation.

For edit and variation requests, require an image path or already available local image file. For edits, pass `--mask` only when the user provides a mask image. If a requested parameter is unsupported by the chosen operation, omit the parameter and keep the user's visual instruction in `--prompt`.

## Waiting Behavior

Always submit through the async endpoints and wait in the foreground by default. Do not add `--no-wait` unless the user explicitly asks only to submit, not to wait, or wants to do other work immediately.

The script prints `task_id`, writes a local state file, then polls every `10` seconds by default. It prints `task_status` and `progress` on each poll when the gateway returns progress fields. If the user interrupts waiting, keep the task id and tell them they can ask for the result later; answer later status/result requests by running `status --task-id <task_id> --markdown`.

## Commands

Text to image:

```bash
python3 scripts/studyhard_image_gen.py submit-generation --prompt "<prompt>" --model "<model>" --size "1024x1024" --n 1
```

Optional generation parameters: `--quality`, `--background`, `--output-format`, `--response-format`, `--user`.

Edit image:

```bash
python3 scripts/studyhard_image_gen.py submit-edit --image "<path>" --prompt "<prompt>" --model "<model>" --size "1024x1024"
```

Optional edit parameters: `--mask`, `--quality`, `--background`, `--response-format`, `--user`.

Variation:

```bash
python3 scripts/studyhard_image_gen.py submit-variation --image "<path>" --model "<model>" --size "1024x1024" --n 4
```

Optional variation parameters: `--response-format`, `--user`.

Submit without waiting only when explicitly requested:

```bash
python3 scripts/studyhard_image_gen.py submit-generation --prompt "<prompt>" --no-wait
```

Check status or resume after interruption:

```bash
python3 scripts/studyhard_image_gen.py status --task-id "<task_id>" --markdown
```

## Returning Results

When `status` or `watch` reports `succeed`, the script caches generated images locally under the task state directory and prints local absolute paths first. Render every local path as a Markdown image:

```markdown
![generated image](/absolute/path/to/generated-image.png)
```

If local caching fails for an image, the script falls back to printing its remote URL. If `result_url` contains comma-separated URLs, split them and render each image separately. If a task is still `submitted` or `processing`, say it is still generating and include the task id.

## Defaults

Use these defaults unless the user says otherwise:

- `size`: `1024x1024`
- `n` for generation: `1`
- `model`: `gpt-image-2`
- `n` for variations: `4`
- poll interval: `10` seconds
- watcher timeout: `900` seconds
