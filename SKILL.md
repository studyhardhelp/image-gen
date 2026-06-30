---
name: studyhard-image-gen
description: Use this skill when a user asks Codex to generate images from text, edit or modify an existing image, or create image variations / one-image-to-many-images using the StudyHard token-api OpenAI-compatible image gateway. It submits asynchronous image tasks to /async/v1/images/generations, /edits, or /variations, starts a background poller for /v1/task/{taskId}, records task state locally, and returns generated image URLs or Markdown image previews without blocking Codex while generation runs.
---

# StudyHard Image Gen

Use the bundled Python script for all image gateway calls. Do not hand-roll curl unless the script is unavailable.

## Configuration

Read image gateway configuration from the standard Codex config location:

- `CODEX_HOME/config.toml`, or `~/.codex/config.toml` when `CODEX_HOME` is unset.
- `CODEX_HOME/auth.json`, or `~/.codex/auth.json` when `CODEX_HOME` is unset.

Use the active `model_provider` from `config.toml`. Read `base_url` and bearer token from that provider, falling back to `auth.json` key `OPENAI_API_KEY`. Do not use the Codex top-level `model` as the image model. The default image model is `gpt-image-2`.

Environment variables are optional overrides only:

- `STUDYHARD_IMAGE_BASE_URL`: override Codex provider `base_url`.
- `STUDYHARD_IMAGE_API_KEY`: override Codex provider/auth API key.
- `STUDYHARD_IMAGE_MODEL`: override the default image model `gpt-image-2`.
- `STUDYHARD_IMAGE_OUT_DIR`: directory for local task state files. Defaults to the system temporary directory under `studyhard-images`.

If required config is missing, explain that Codex config must contain an active provider `base_url` and API key.

## Intent Routing

- Text-to-image, draw, create, generate from prompt: run `submit-generation`.
- Edit, modify, replace, remove, or transform an existing image: run `submit-edit` with `--image` and optional `--mask`.
- Variation, one image to many images, make similar images, or image-to-image variants: run `submit-variation` with `--image`.
- User asks whether an image is ready, asks for a result, or gives a task id: run `status`.

## Non-Blocking Behavior

Always submit through the async endpoints and start the watcher with `--watch` unless the user explicitly asks only to submit. After submission, immediately report the `task_id` and then repeat the `message` field returned by the script exactly.

Do not wait for image completion in the current turn unless the user explicitly asks to wait. The background watcher keeps polling independently and writes a state JSON file.

## Commands

Text to image:

```bash
python scripts/studyhard_image_gen.py submit-generation --prompt "<prompt>" --model "<model>" --size "1024x1024" --n 1 --watch
```

Edit image:

```bash
python scripts/studyhard_image_gen.py submit-edit --image "<path>" --prompt "<prompt>" --model "<model>" --size "1024x1024" --watch
```

Variation:

```bash
python scripts/studyhard_image_gen.py submit-variation --image "<path>" --model "<model>" --size "1024x1024" --n 4 --watch
```

Check status:

```bash
python scripts/studyhard_image_gen.py status --task-id "<task_id>" --markdown
```

Foreground wait only when explicitly requested:

```bash
python scripts/studyhard_image_gen.py watch --task-id "<task_id>" --timeout 180
```

## Returning Results

When `status` or `watch` reports `succeed`, render every URL as a Markdown image:

```markdown
![generated image](https://example.com/image.png)
```

If `result_url` contains comma-separated URLs, split them and render each image separately. If a task is still `submitted` or `processing`, say it is still generating and include the task id.

## Defaults

Use these defaults unless the user says otherwise:

- `size`: `1024x1024`
- `n` for generation: `1`
- `model`: `gpt-image-2`
- `n` for variations: `4`
- poll interval: `5` seconds
- watcher timeout: `900` seconds