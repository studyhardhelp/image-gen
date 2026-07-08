---
name: image-gen
description: Use this skill when a user asks Codex to generate images from text, or edit or modify an existing image using the StudyHard token-api OpenAI-compatible image gateway. It submits asynchronous image tasks to image generation and edit endpoints, waits by default with foreground polling, records task state locally, prints task progress, and returns generated image URLs or Markdown image previews.
---

# StudyHard Image Gen

Use the bundled Python entrypoint for all image gateway calls. Do not hand-roll curl unless the entrypoint is unavailable.

## User-Facing Language

When talking to the user, describe this capability as the `image-gen` skill. Do not mention implementation details such as "script", "tool path", "Python file", or "loaded files" unless the user explicitly asks how the skill works. Say "我用 image-gen skill 生成" or "我正在用 image-gen skill 查询进度" instead of saying you are using a script.

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
- `STUDYHARD_IMAGE_OUT_DIR`: directory for local task state files. Defaults to `studyhard-images` in the current project directory.

If required config is missing, explain that Codex config must contain an API key, or use the `STUDYHARD_IMAGE_API_KEY` environment override.

## Intent Routing

- Text-to-image, draw, create, generate from prompt: run `submit-generation`.
- Edit, modify, replace, remove, or transform an existing image: run `submit-edit` with `--image` and optional `--mask`.
- User asks whether an image is ready, asks for a result, or gives a task id: run `status`.

## Parameter Extraction

Extract explicit user requirements into CLI arguments instead of burying them in the prompt:

- Count: extract Arabic and Chinese counts into `--n <count>`. Examples: "生成 3 张" -> `--n 3`; "三张/3张/三幅/三个版本" -> `--n 3`; "一张/一幅/一个版本" -> `--n 1`; "两张/二张/两幅/两个版本" -> `--n 2`; "四张/四幅/四个版本" -> `--n 4`. Use generation default `1` only when the user does not specify a count. For text-to-image generation, `--n` means how many separate image tasks the skill should submit; when `--n > 1`, the entrypoint submits that many generation requests, each with gateway JSON `"n": 1`, and polls all returned task ids together. Do not rely on one gateway generation request with `"n" > 1`.
- Size/aspect for generation: pass every explicit size-related parameter that can be extracted; do not make `--size` mutually exclusive with `--resolution` or `--ratio`. If the user gives an exact pixel size such as `1280x720`, `1280×720`, `1280*720`, `1280＊720`, `1280 乘 720`, or `1280 by 720`, pass `--size 1280x720`. The entrypoint also parses these exact pixel-size forms from `--prompt` as a fallback when Codex misses the CLI argument. When the user gives `1k`, `2k`, or `4k`, pass `--resolution 1k|2k|4k`. When the user gives a ratio, pass `--ratio`; supported ratios include `1:1`, `3:4`, `4:3`, `16:9`, `9:16`, `5:4`, `21:9`, `3:2`, `4:5`, and `2:3`. If the user gives only a resolution, assume `--ratio 1:1`. If no exact pixel size is specified, use the default `--size 1024x1024` unless a better explicit size was requested.
- Quality: "高清", "high quality", "精细" -> `--quality high`; "快速", "低成本", "草图" -> `--quality low`; "standard/hd" -> pass the explicit value.
- Background: "透明背景", "transparent background", "抠图/无背景" -> `--background transparent`; "不透明背景" -> `--background opaque`.
- Output format: "PNG/JPEG/WebP" -> generation `--output-format png|jpeg|webp` when requesting generated image encoding. For OpenAI-compatible URL responses, keep `--response-format url` unless the user explicitly requests base64 JSON.
- User field: only pass `--user` when the user explicitly provides an end-user identifier.
- Model: use `gpt-image-2` unless the user names another available model or the gateway requires one for a specific operation.

For edit requests, require an image path or already available local image file. Pass `--mask` only when the user provides a mask image. If a requested parameter is unsupported by the chosen operation, omit the parameter and keep the user's visual instruction in `--prompt`.

## Waiting Behavior

Always submit through the async endpoints and wait in the foreground by default. Do not add `--no-wait` unless the user explicitly asks only to submit, not to wait, or wants to do other work immediately.

The command prints `task_id` for a single-image request, or all real server `task_ids` for a multi-image generation request. It may keep an internal local batch state, but do not show or mention that internal batch id to the user. It writes local state files, then polls every `15` seconds by default. The minimum accepted poll interval is `15` seconds. It prints `task_status` and `progress` on each poll when the gateway returns progress fields. For multi-image generation, each poll prints progress for every task id. In user-facing updates during waiting, list each real task id on its own line with the latest progress, for example "任务 873... 正在生成，进度 40%". If the command reports `progress: unknown`, say that the gateway has not returned a progress percentage yet. Do not summarize multi-image progress as only one aggregate percentage when per-task progress is available. If the user interrupts waiting, keep the task ids and tell them they can ask for the result later; answer later status/result requests by running `status --task-id <task_id> [<task_id> ...] --markdown`.

## Commands

Text to image:

```bash
python3 scripts/studyhard_image_gen.py submit-generation --prompt "<prompt>" --model "<model>" --resolution "1k" --ratio "1:1" --n 1
```

Optional generation parameters: `--resolution`, `--ratio`, `--size`, `--quality`, `--background`, `--output-format`, `--response-format`, `--user`.

Edit image:

```bash
python3 scripts/studyhard_image_gen.py submit-edit --image "<path>" --prompt "<prompt>" --model "<model>" --size "1024x1024"
```

Optional edit parameters: `--mask`, `--quality`, `--background`, `--response-format`, `--user`.

Submit without waiting only when explicitly requested:

```bash
python3 scripts/studyhard_image_gen.py submit-generation --prompt "<prompt>" --no-wait
```

Check status or resume after interruption:

```bash
python3 scripts/studyhard_image_gen.py status --task-id "<task_id>" --markdown
```

Check multiple image tasks:

```bash
python3 scripts/studyhard_image_gen.py status --task-id "<task_id_1>" "<task_id_2>" "<task_id_3>" --markdown
```

## Returning Results

When `status` or `watch` reports `succeed`, the command stores task state JSON and generated images under `studyhard-images/YYYYMMDD/`, where `YYYYMMDD` is the current local date. Use the filename from the result URL for images. Render every local path as a Markdown image linked to the same local file, followed by a direct local download link. Fall back to the remote result URL only when local caching fails:

```markdown
[![generated image](/absolute/path/to/studyhard-images/20260708/result-file.png)](/absolute/path/to/studyhard-images/20260708/result-file.png)
[download result-file.png](/absolute/path/to/studyhard-images/20260708/result-file.png)
```

If local caching fails for an image, the command falls back to printing its remote URL. If `result_url` contains comma-separated URLs, split them and render each image separately. If a task is still `submitted` or `processing`, say it is still generating and include the task id.

## Defaults

Use these defaults unless the user says otherwise:

- `size`: `1024x1024`
- `n` for generation: `1`
- `model`: `gpt-image-2`
- poll interval: `15` seconds
- watcher timeout: `900` seconds
