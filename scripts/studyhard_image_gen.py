#!/usr/bin/env python3
"""Async client for the StudyHard token-api image gateway.

Uses only the Python standard library so the skill can run in Codex without
installing dependencies.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import posixpath
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib import error, parse, request

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]

DEFAULT_SIZE = "1024x1024"
DEFAULT_INTERVAL = 10
DEFAULT_TIMEOUT = 900
DEFAULT_IMAGE_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = "https://api.studyhard.help"


class UserFacingError(Exception):
    """Error that can be shown directly to a Codex user."""


def fail(message: str) -> None:
    raise UserFacingError(message)

def env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def codex_home() -> Path:
    configured = env("CODEX_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def load_codex_config() -> Dict[str, Any]:
    config_path = codex_home() / "config.toml"
    if not config_path.exists():
        return {}
    if tomllib is None:
        fail("Python 3.11 or newer is required to read Codex config.toml. Use python3.11+, or set STUDYHARD_IMAGE_API_KEY.")
    try:
        with config_path.open("rb") as fh:
            return tomllib.load(fh)
    except Exception as exc:
        fail(f"Could not read Codex config at {config_path}: {exc}")


def load_codex_auth() -> Dict[str, Any]:
    auth_path = codex_home() / "auth.json"
    if not auth_path.exists():
        return {}
    try:
        return json.loads(auth_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        fail(f"Could not parse Codex auth file at {auth_path}. Check that auth.json contains valid JSON.")


def active_provider_config(config: Dict[str, Any]) -> Dict[str, Any]:
    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider_name, str) or not isinstance(providers, dict):
        return {}
    provider = providers.get(provider_name)
    return provider if isinstance(provider, dict) else {}


def require_config() -> Tuple[str, str]:
    config = load_codex_config()
    auth = load_codex_auth()
    provider = active_provider_config(config)

    base_url = env("STUDYHARD_IMAGE_BASE_URL", DEFAULT_BASE_URL)
    api_key = (
        env("STUDYHARD_IMAGE_API_KEY")
        or provider.get("experimental_bearer_token")
        or provider.get("api_key")
        or provider.get("openai_api_key")
        or auth.get("OPENAI_API_KEY")
    )

    missing = []
    if not api_key:
        missing.append("Codex config/auth API key")
    if missing:
        fail(
            "Missing image gateway configuration: "
            + ", ".join(missing)
            + ". Configure an API key in CODEX_HOME/config.toml or CODEX_HOME/auth.json, or set STUDYHARD_IMAGE_API_KEY."
        )
    return str(base_url).rstrip("/"), str(api_key)


def default_out_dir() -> Path:
    configured = env("STUDYHARD_IMAGE_OUT_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "studyhard-images"


def task_dir(task_id: str, out_dir: Optional[Path] = None, create: bool = False) -> Path:
    target = out_dir or default_out_dir()
    path = target / task_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(task_id: str, out_dir: Optional[Path] = None, create: bool = False) -> Path:
    target = out_dir or default_out_dir()
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target / f"{task_id}.json"


def write_state(task_id: str, data: Dict[str, Any], out_dir: Optional[Path] = None) -> Path:
    path = state_path(task_id, out_dir, create=True)
    data.setdefault("task_id", task_id)
    data["updated_at"] = int(time.time())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def read_state(task_id: str, out_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = state_path(task_id, out_dir, create=True)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def default_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Codex/StudyHardImageGen",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
    }


def http_json(method: str, url: str, api_key: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = None
    headers = default_headers(api_key)
    if body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=utf-8"
    req = request.Request(url, data=payload, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                fail(f"Image gateway returned non-JSON response from {url}: {raw[:500]}")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"Image gateway request failed: HTTP {exc.code} {exc.reason}. Response: {raw[:1000]}")
    except error.URLError as exc:
        fail(f"Could not reach image gateway at {url}: {exc.reason}")
    except TimeoutError:
        fail(f"Timed out while connecting to image gateway at {url}")


def encode_multipart(fields: Dict[str, Any], files: Dict[str, Path]) -> Tuple[bytes, str]:
    boundary = "----studyhard-codex-" + uuid.uuid4().hex
    chunks: List[bytes] = []

    def add(value: str) -> None:
        chunks.append(value.encode("utf-8"))

    for name, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            values: Iterable[Any] = value
        else:
            values = (value,)
        for item in values:
            add(f"--{boundary}\r\n")
            add(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
            add(str(item))
            add("\r\n")

    for name, path in files.items():
        content = path.read_bytes()
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        add(f"--{boundary}\r\n")
        add(f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n')
        add(f"Content-Type: {mime}\r\n\r\n")
        chunks.append(content)
        add("\r\n")

    add(f"--{boundary}--\r\n")
    return b"".join(chunks), boundary


def http_multipart(url: str, api_key: str, fields: Dict[str, Any], files: Dict[str, Path]) -> Dict[str, Any]:
    payload, boundary = encode_multipart(fields, files)
    headers = default_headers(api_key)
    headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    req = request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                fail(f"Image gateway returned non-JSON response from {url}: {raw[:500]}")
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        fail(f"Image gateway upload failed: HTTP {exc.code} {exc.reason}. Response: {raw[:1000]}")
    except error.URLError as exc:
        fail(f"Could not reach image gateway at {url}: {exc.reason}")
    except TimeoutError:
        fail(f"Timed out while uploading to image gateway at {url}")


def route_url(base_url: str, route_path: str) -> str:
    base = base_url.rstrip("/")
    path = route_path if route_path.startswith("/") else "/" + route_path
    return base + path


def model_or_default(model: Optional[str]) -> str:
    if model:
        return model
    configured = env("STUDYHARD_IMAGE_MODEL")
    if configured:
        return configured
    return DEFAULT_IMAGE_MODEL


def optional_fields(args: argparse.Namespace, names: Iterable[str]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            fields[name] = value
    return fields


def add_optional_body_fields(target: Dict[str, Any], args: argparse.Namespace, names: Iterable[str]) -> None:
    for name in names:
        value = getattr(args, name, None)
        if value is not None:
            target[name] = value


def submit_generation(args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        base_url, api_key = env("STUDYHARD_IMAGE_BASE_URL", DEFAULT_BASE_URL), ""
    else:
        base_url, api_key = require_config()
    body: Dict[str, Any] = {
        "model": model_or_default(args.model),
        "prompt": args.prompt,
        "size": args.size,
        "n": args.n,
    }
    add_optional_body_fields(body, args, ("quality", "background", "output_format", "response_format", "user"))
    if args.dry_run:
        return {"dry_run": True, "method": "POST", "url": route_url(base_url, "/async/v1/images/generations"), "json": body}
    return http_json("POST", route_url(base_url, "/async/v1/images/generations"), api_key, body)


def submit_edit(args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        base_url, api_key = env("STUDYHARD_IMAGE_BASE_URL", DEFAULT_BASE_URL), ""
    else:
        base_url, api_key = require_config()
    image = Path(args.image)
    if not image.exists():
        fail(f"Image file not found: {image}")
    if not image.is_file():
        fail(f"Image path is not a file: {image}")
    fields: Dict[str, Any] = {
        "model": model_or_default(args.model),
        "prompt": args.prompt,
        "size": args.size,
        "n": args.n,
    }
    fields.update(optional_fields(args, ("quality", "background", "response_format", "user")))
    files = {"image": image}
    if args.mask:
        mask = Path(args.mask)
        if not mask.exists():
            fail(f"Mask file not found: {mask}")
        if not mask.is_file():
            fail(f"Mask path is not a file: {mask}")
        files["mask"] = mask
    if args.dry_run:
        return {
            "dry_run": True,
            "method": "POST",
            "url": route_url(base_url, "/async/v1/images/edits"),
            "fields": fields,
            "files": {name: str(path) for name, path in files.items()},
        }
    return http_multipart(route_url(base_url, "/async/v1/images/edits"), api_key, fields, files)


def submit_variation(args: argparse.Namespace) -> Dict[str, Any]:
    if args.dry_run:
        base_url, api_key = env("STUDYHARD_IMAGE_BASE_URL", DEFAULT_BASE_URL), ""
    else:
        base_url, api_key = require_config()
    image = Path(args.image)
    if not image.exists():
        fail(f"Image file not found: {image}")
    if not image.is_file():
        fail(f"Image path is not a file: {image}")
    fields: Dict[str, Any] = {
        "model": model_or_default(args.model),
        "size": args.size,
        "n": args.n,
    }
    fields.update(optional_fields(args, ("response_format", "user")))
    if args.dry_run:
        return {
            "dry_run": True,
            "method": "POST",
            "url": route_url(base_url, "/async/v1/images/variations"),
            "fields": fields,
            "files": {"image": str(image)},
        }
    return http_multipart(route_url(base_url, "/async/v1/images/variations"), api_key, fields, {"image": image})


def extract_urls(data: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    result = data.get("result")
    if isinstance(result, dict):
        value = result.get("result_url")
        if isinstance(value, str):
            urls.extend([part.strip() for part in value.split(",") if part.strip()])
    value = data.get("result_url")
    if isinstance(value, str):
        urls.extend([part.strip() for part in value.split(",") if part.strip()])
    return list(dict.fromkeys(urls))


def nested_value(data: Dict[str, Any], keys: Iterable[str]) -> Optional[Any]:
    for key in keys:
        current: Any = data
        found = True
        for part in key.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current not in (None, ""):
            return current
    return None


def extract_progress(data: Dict[str, Any]) -> Optional[int]:
    status = str(data.get("task_status", "")).lower()
    if status == "succeed":
        return 100
    value = nested_value(data, (
        "progress",
        "task_progress",
        "percentage",
        "percent",
        "result.progress",
        "result.task_progress",
        "result.percentage",
        "result.percent",
        "output.progress",
        "output.task_progress",
        "output.percentage",
        "output.percent",
    ))
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip().rstrip("%")
        progress = int(float(value))
        return max(0, min(progress, 100))
    except (TypeError, ValueError):
        return None


def format_progress(data: Dict[str, Any]) -> str:
    progress = extract_progress(data)
    return "unknown" if progress is None else f"{progress}%"


def extension_from_url(url: str) -> str:
    path = parse.urlparse(url).path
    ext = posixpath.splitext(path)[1].lower()
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return ext
    return ".png"


def extension_from_content_type(content_type: str, fallback: str) -> str:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "image/jpeg":
        return ".jpg"
    ext = mimetypes.guess_extension(media_type)
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return ext
    return fallback


def download_image(url: str, target_without_ext: Path) -> Path:
    fallback_ext = extension_from_url(url)
    existing = sorted(target_without_ext.parent.glob(target_without_ext.name + ".*"))
    for path in existing:
        if path.is_file() and path.stat().st_size > 0:
            return path.resolve()

    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": default_headers("unused")["User-Agent"],
    }
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=120) as resp:
        content_type = resp.headers.get("Content-Type", "")
        ext = extension_from_content_type(content_type, fallback_ext)
        target = target_without_ext.with_suffix(ext)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(resp.read())
        tmp.replace(target)
        return target.resolve()


def cache_result_images(data: Dict[str, Any], task_id: str, out_dir: Optional[Path] = None) -> Dict[str, Any]:
    urls = data.get("result_urls") if isinstance(data.get("result_urls"), list) else extract_urls(data)
    cached: List[Optional[str]] = []
    errors: List[str] = []
    if not urls:
        data["result_urls"] = []
        data["local_image_paths"] = []
        return data

    images_dir = task_dir(task_id, out_dir, create=True)
    for index, url in enumerate(urls, start=1):
        try:
            path = download_image(str(url), images_dir / f"image-{index}")
            cached.append(str(path))
        except Exception as exc:
            cached.append(None)
            errors.append(f"{url}: {exc}")

    data["result_urls"] = list(urls)
    data["local_image_paths"] = cached
    if errors:
        data["local_image_errors"] = errors
    else:
        data.pop("local_image_errors", None)
    return data


def query_task(task_id: str) -> Dict[str, Any]:
    base_url, api_key = require_config()
    return http_json("GET", route_url(base_url, f"/v1/task/{task_id}"), api_key)


def watch_task(args: argparse.Namespace) -> Dict[str, Any]:
    task_id = args.task_id
    out_dir = Path(args.out_dir) if args.out_dir else default_out_dir()
    deadline = time.time() + args.timeout if args.timeout and args.timeout > 0 else None
    last: Dict[str, Any] = {"task_id": task_id, "task_status": "submitted"}

    while True:
        try:
            last = query_task(task_id)
            status = str(last.get("task_status", "unknown"))
            urls = extract_urls(last)
            state = dict(last)
            state["result_urls"] = urls
            state["progress"] = extract_progress(state)
            if status == "succeed":
                cache_result_images(state, task_id, out_dir)
            write_state(task_id, state, out_dir)
            if getattr(args, "progress", False):
                print(f"task_id: {task_id} task_status: {status} progress: {format_progress(state)}", flush=True)
            if status in ("succeed", "failed"):
                return state
        except Exception as exc:  # keep watcher alive across transient failures
            state = {"task_id": task_id, "task_status": "poll_error", "error": str(exc)}
            write_state(task_id, state, out_dir)
            last = state
            if getattr(args, "progress", False):
                print(f"task_id: {task_id} task_status: poll_error progress: unknown error: {exc}", flush=True)

        if deadline is not None and time.time() >= deadline:
            state = dict(last)
            state["task_status"] = "timeout"
            state["error"] = f"Timed out after {args.timeout} seconds"
            write_state(task_id, state, out_dir)
            return state
        time.sleep(args.interval)


def spawn_watcher(task_id: str, interval: int, timeout: int, out_dir: Optional[str]) -> None:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "watch",
        "--task-id",
        task_id,
        "--interval",
        str(interval),
        "--timeout",
        str(timeout),
    ]
    if out_dir:
        cmd.extend(["--out-dir", out_dir])

    stdout = subprocess.DEVNULL
    stderr = subprocess.DEVNULL
    kwargs: Dict[str, Any] = {"stdout": stdout, "stderr": stderr, "stdin": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        fail(f"Task was submitted, but the background watcher could not be started: {exc}")


def print_json(data: Dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def zh(text: str) -> str:
    return text.encode("utf-8").decode("unicode_escape")


def print_status_for_codex(data: Dict[str, Any]) -> None:
    status = data.get("task_status", "unknown")
    task_id = data.get("task_id", "")
    urls = data.get("result_urls") if isinstance(data.get("result_urls"), list) else extract_urls(data)
    local_paths = data.get("local_image_paths") if isinstance(data.get("local_image_paths"), list) else []
    print(f"task_id: {task_id}")
    print(f"task_status: {status}")
    print(f"progress: {format_progress(data)}")
    if status == "succeed" and (local_paths or urls):
        print(zh("\\u751f\\u6210\\u5b8c\\u6210\\uff1a"))
        for index, url in enumerate(urls):
            path = local_paths[index] if index < len(local_paths) else None
            print(f"![generated image]({path or url})")
        if data.get("local_image_errors"):
            print(zh("\\u90e8\\u5206\\u56fe\\u7247\\u672c\\u5730\\u7f13\\u5b58\\u5931\\u8d25\\uff0c\\u5df2\\u56de\\u9000\\u5230\\u8fdc\\u7a0b URL\\u3002"))
    elif status in ("submitted", "processing", "poll_error", "unknown"):
        print(zh("\\u6b63\\u5728\\u540e\\u53f0\\u751f\\u6210\\u3002\\u4f60\\u53ef\\u4ee5\\u7ee7\\u7eed\\u8ba9\\u6211\\u505a\\u5176\\u4ed6\\u4e8b\\uff1b\\u751f\\u6210\\u5b8c\\u6210\\u540e\\u6211\\u4f1a\\u5728\\u540e\\u7eed\\u6d88\\u606f\\u4e2d\\u5c55\\u793a\\u56fe\\u7247\\uff0c\\u6216\\u4f60\\u968f\\u65f6\\u95ee\\u201c\\u56fe\\u7247\\u597d\\u4e86\\u6ca1\\u201d\\u3002"))
    elif status == "failed":
        print(zh("\\u751f\\u6210\\u5931\\u8d25\\uff1a") + str(data.get("status_msg") or data.get("error") or "unknown error"))
    elif status == "timeout":
        print(zh("\\u8f6e\\u8be2\\u8d85\\u65f6\\uff1a") + str(data.get("error") or "timeout"))


def handle_submit(args: argparse.Namespace, fn) -> None:
    result = fn(args)
    if result.get("dry_run"):
        print_json(result)
        return
    task_id = result.get("task_id")
    if not task_id:
        print_json(result)
        fail("Image gateway did not return task_id. The raw response was printed above.")
    out_dir = Path(args.out_dir) if args.out_dir else default_out_dir()
    state = dict(result)
    state.setdefault("task_status", "submitted")
    state["result_urls"] = []
    path = write_state(task_id, state, out_dir)
    if args.no_wait:
        if args.watch:
            spawn_watcher(task_id, args.interval, args.timeout, str(out_dir))
        print_json({
            "task_id": task_id,
            "task_status": state.get("task_status"),
            "state_file": str(path),
            "watch_started": bool(args.watch),
            "message": zh("\\u4efb\\u52a1\\u5df2\\u63d0\\u4ea4\\u3002\\u4f60\\u53ef\\u4ee5\\u7a0d\\u540e\\u7528 status \\u67e5\\u8be2\\u7ed3\\u679c\\u3002"),
        })
        return

    print_json({
        "task_id": task_id,
        "task_status": state.get("task_status"),
        "state_file": str(path),
        "message": zh("\\u4efb\\u52a1\\u5df2\\u63d0\\u4ea4\\uff0c\\u5f00\\u59cb\\u524d\\u53f0\\u8f6e\\u8be2\\u3002\\u5982\\u679c\\u4f60\\u7ec8\\u6b62\\u7b49\\u5f85\\uff0c\\u540e\\u7eed\\u53ef\\u4ee5\\u7528 task_id \\u7ee7\\u7eed\\u67e5\\u8be2\\u3002"),
    })
    watch_args = argparse.Namespace(
        task_id=task_id,
        interval=args.interval,
        timeout=args.timeout,
        out_dir=str(out_dir),
        progress=True,
    )
    final_state = watch_task(watch_args)
    if str(final_state.get("task_status", "unknown")) == "succeed":
        print_status_for_codex(final_state)
    else:
        print_json(final_state)

def handle_status(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir) if args.out_dir else default_out_dir()
    cached = read_state(args.task_id, out_dir)
    if args.local and cached:
        data = cached
    else:
        try:
            remote = query_task(args.task_id)
            remote["result_urls"] = extract_urls(remote)
            remote["progress"] = extract_progress(remote)
            if str(remote.get("task_status", "unknown")) == "succeed":
                cache_result_images(remote, args.task_id, out_dir)
            data = remote
            write_state(args.task_id, data, out_dir)
        except Exception:
            if not cached:
                raise
            data = cached
    if str(data.get("task_status", "unknown")) == "succeed":
        cache_result_images(data, args.task_id, out_dir)
        write_state(args.task_id, data, out_dir)
    if args.markdown:
        print_status_for_codex(data)
    else:
        print_json(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="StudyHard async image gateway client")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_submit(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model")
        p.add_argument("--size", default=DEFAULT_SIZE)
        p.add_argument("--n", type=int, default=1)
        p.add_argument("--response-format", choices=["url", "b64_json"])
        p.add_argument("--user")
        p.add_argument("--dry-run", action="store_true", help="Print the gateway request without submitting")
        p.add_argument("--no-wait", action="store_true", help="Submit the task and return immediately")
        p.add_argument("--watch", action="store_true", help="With --no-wait, start a detached background watcher")
        p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
        p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
        p.add_argument("--out-dir")

    gen = sub.add_parser("submit-generation")
    add_common_submit(gen)
    gen.add_argument("--prompt", required=True)
    gen.add_argument("--quality", choices=["auto", "low", "medium", "high", "standard", "hd"])
    gen.add_argument("--background", choices=["auto", "transparent", "opaque"])
    gen.add_argument("--output-format", choices=["png", "jpeg", "webp"])
    gen.set_defaults(func=lambda a: handle_submit(a, submit_generation))

    edit = sub.add_parser("submit-edit")
    add_common_submit(edit)
    edit.add_argument("--prompt", required=True)
    edit.add_argument("--image", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--quality", choices=["auto", "low", "medium", "high", "standard", "hd"])
    edit.add_argument("--background", choices=["auto", "transparent", "opaque"])
    edit.set_defaults(func=lambda a: handle_submit(a, submit_edit))

    variation = sub.add_parser("submit-variation")
    add_common_submit(variation)
    variation.set_defaults(n=4)
    variation.add_argument("--image", required=True)
    variation.set_defaults(func=lambda a: handle_submit(a, submit_variation))

    watch = sub.add_parser("watch")
    watch.add_argument("--task-id", required=True)
    watch.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    watch.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    watch.add_argument("--out-dir")
    watch.add_argument("--progress", action="store_true", help="Print progress on every poll")
    watch.set_defaults(func=lambda a: print_json(watch_task(a)))

    status = sub.add_parser("status")
    status.add_argument("--task-id", required=True)
    status.add_argument("--out-dir")
    status.add_argument("--local", action="store_true", help="Read local state only when available")
    status.add_argument("--markdown", action="store_true", help="Print a Codex-ready status and image Markdown")
    status.set_defaults(func=handle_status)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except UserFacingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Error: interrupted by user", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
