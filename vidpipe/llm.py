import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import cfg, env
from .util import say, warn


class LLMError(RuntimeError):
    pass


@dataclass
class Result:
    text: str
    data: object = None
    usd: float = 0.0
    usage: dict = field(default_factory=dict)
    engine: str = ""
    model: str = ""
    seen_urls: list = field(default_factory=list)


def find_cli():
    custom = cfg()["llm"].get("cli_path")
    if custom and Path(custom).exists():
        return custom
    for cand in (shutil.which("claude"), Path.home() / ".local" / "bin" / "claude.exe"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


def pick_engine():
    engine = cfg()["llm"]["engine"]
    if engine != "auto":
        return engine
    if env("ANTHROPIC_API_KEY"):
        return "api"
    if find_cli():
        return "cli"
    raise SystemExit("No Claude access. Put ANTHROPIC_API_KEY in .env or install Claude Code and log in.")


def complete(step, system, prompt, schema=None, web=False, effort=None, max_tokens=32000, log_dir=None):
    """One Claude call. Returns Result with .text and, when a schema is given, parsed .data."""
    c = cfg()["llm"]
    effort = effort or c["effort"].get(step, "high")
    engine = pick_engine()
    started = time.time()
    say(f"  claude ({engine}, {c['model']}, effort {effort}){' + web' if web else ''}: {step} ...")
    if engine == "api":
        res = _api(system, prompt, schema, web, effort, max_tokens)
    else:
        res = _cli(system, prompt, schema, web, effort)
    if schema is not None and res.data is None:
        res.data = parse_json(res.text)
    say(f"  done in {time.time() - started:.0f}s, ~${res.usd:.3f}")
    if log_dir:
        _log(log_dir, step, res, effort)
    return res


def parse_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"Claude did not return valid JSON: {e}") from e


def _price(model, usage):
    p = cfg()["prices"].get(model)
    if not p:
        return 0.0
    usd = (
        usage.get("input_tokens", 0) * p["in"]
        + usage.get("output_tokens", 0) * p["out"]
        + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
    ) / 1e6
    usd += usage.get("web_search_requests", 0) * cfg()["prices"]["web_search_per_1000"] / 1000
    return usd


def _api(system, prompt, schema, web, effort, max_tokens):
    import anthropic

    c = cfg()["llm"]
    client = anthropic.Anthropic(timeout=c["timeout_minutes"] * 60, max_retries=3)
    params = {
        "model": c["model"],
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": effort},
    }
    if schema is not None:
        params["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    if web:
        params["tools"] = [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": c["web_search_max_uses"]},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": c["web_fetch_max_uses"]},
        ]
    extra = {}
    if c.get("fallbacks"):
        extra = {
            "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
            "extra_body": {"fallbacks": "default"},
        }

    messages = [{"role": "user", "content": prompt}]
    texts, urls = [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0, "web_search_requests": 0}
    model_used = c["model"]
    for _ in range(8):
        try:
            with client.messages.stream(messages=messages, **params, **extra) as stream:
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic API key rejected. Check ANTHROPIC_API_KEY in .env.") from e
        except anthropic.BadRequestError as e:
            raise LLMError(f"Bad request: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Network error talking to the Claude API.") from e
        model_used = msg.model or model_used
        u = msg.usage
        for k in list(usage):
            if k == "web_search_requests":
                stu = getattr(u, "server_tool_use", None)
                usage[k] += getattr(stu, "web_search_requests", 0) or 0
            else:
                usage[k] += getattr(u, k, 0) or 0
        for block in msg.content:
            btype = getattr(block, "type", "")
            if btype == "text":
                texts.append(block.text)
            elif btype == "web_search_tool_result":
                content = getattr(block, "content", None)
                if isinstance(content, list):
                    urls += [getattr(r, "url", "") for r in content]
            elif btype == "web_fetch_tool_result":
                content = getattr(block, "content", None)
                url = getattr(content, "url", None)
                if url:
                    urls.append(url)
        if msg.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": [b.to_dict() for b in msg.content]}]
            continue
        if msg.stop_reason == "refusal":
            details = getattr(msg, "stop_details", None)
            raise LLMError(f"Claude declined this request ({getattr(details, 'category', None)}): "
                           f"{getattr(details, 'explanation', '')}")
        if msg.stop_reason == "max_tokens":
            raise LLMError("Output hit max_tokens; raise max_tokens for this step.")
        break
    text = "".join(texts) if schema is None else (texts[-1] if texts else "")
    return Result(text=text, usd=_price(c["model"], usage), usage=usage, engine="api",
                  model=model_used, seen_urls=[u for u in urls if u])


def _clean_env():
    keep = {"CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX"}
    out = {}
    for k, v in os.environ.items():
        if k in keep:
            out[k] = v
        elif k.startswith(("CLAUDE_CODE_", "CLAUDE_AGENT_SDK", "CLAUDE_PREVIEW")) or k in ("CLAUDECODE", "CLAUDE_PID"):
            continue
        else:
            out[k] = v
    return out


def _cli(system, prompt, schema, web, effort):
    c = cfg()["llm"]
    exe = find_cli()
    if not exe:
        raise LLMError("Claude Code CLI not found.")
    cmd = [
        exe, "-p",
        "--output-format", "json",
        "--no-session-persistence",
        "--safe-mode",
        "--model", c["model"],
        "--effort", effort,
        "--system-prompt", system,
        "--max-budget-usd", str(c["max_usd_per_call"]),
        "--permission-mode", "dontAsk",
    ]
    if web:
        cmd += ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch,WebFetch"]
    else:
        cmd += ["--tools", ""]
    if schema is not None:
        cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    try:
        proc = subprocess.run(
            cmd, input=prompt.encode("utf-8"), capture_output=True, env=_clean_env(),
            timeout=c["timeout_minutes"] * 60, cwd=str(Path.home()),
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"Claude CLI timed out after {c['timeout_minutes']} min") from e
    raw = proc.stdout.decode("utf-8", "replace").strip()
    out = None
    for candidate in (raw, raw.splitlines()[-1] if raw else ""):
        try:
            out = json.loads(candidate)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(out, dict):
        err = proc.stderr.decode("utf-8", "replace")[-2000:]
        raise LLMError(f"Claude CLI failed (exit {proc.returncode}): {raw[-1500:]}\n{err}")
    if out.get("is_error") or out.get("subtype") not in (None, "success"):
        raise LLMError(f"Claude CLI error: {out.get('subtype')}: {str(out.get('result', ''))[:1500]}")
    usage = out.get("usage") or {}
    flat = {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "web_search_requests": (usage.get("server_tool_use") or {}).get("web_search_requests", 0),
        "turns": out.get("num_turns", 0),
    }
    data = out.get("structured_output")
    text = out.get("result") or ""
    if data is not None and not text:
        text = json.dumps(data)
    return Result(text=text, data=data, usd=float(out.get("total_cost_usd") or 0.0), usage=flat,
                  engine="cli", model=c["model"])


def _log(log_dir, step, res, effort):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "step": step,
        "engine": res.engine,
        "model": res.model,
        "effort": effort,
        "usd": round(res.usd, 4),
        **res.usage,
    }
    with open(log_dir / "costs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (log_dir / f"{step}-{stamp}.txt").write_text(res.text, encoding="utf-8")
    if res.seen_urls:
        (log_dir / f"{step}-{stamp}-urls.txt").write_text("\n".join(res.seen_urls), encoding="utf-8")


def log_cost(log_dir, step, usd, **extra):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "step": step, "usd": round(usd, 4), **extra}
    with open(log_dir / "costs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def warn_if_long(text, limit=180000):
    if len(text) > limit:
        warn(f"prompt is long ({len(text)} chars)")
