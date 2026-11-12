"""Send one bounded synthetic chat request to a local Ollama server.

This checks local text generation only; it does not execute a tool or test
prompt injection. Start Ollama separately before running this file.
"""

import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, Request, build_opener


URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"
TIMEOUT_SECONDS = 120
MAX_RESPONSE_BYTES = 1024 * 1024


def main():
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content": "Reply with exactly: LOCAL_MODEL_READY"}
        ],
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "options": {
            "num_ctx": 2048,
            "num_predict": 32,
            "temperature": 0,
        },
    }
    request = Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # Keep the fixed loopback request out of any configured HTTP proxy.
    opener = build_opener(ProxyHandler({}))
    started = time.perf_counter()
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Local model response exceeded the byte limit.")
    result = json.loads(raw)
    message = result.get("message", {})
    if (
        result.get("done") is not True
        or not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
        or message.get("tool_calls")
    ):
        raise ValueError("Expected one completed, nonempty assistant text reply.")

    report = {
        "experiment": "local_text_generation_only",
        "requested_model": MODEL,
        "reported_model": result.get("model"),
        "content": message["content"],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "done_reason": result.get("done_reason"),
        "input_tokens": result.get("prompt_eval_count"),
        "output_tokens": result.get("eval_count"),
        "model_calls": 1,
        "dispatch_attempts": 0,
        "handler_calls": 0,
        "limits": {
            "timeout_seconds": TIMEOUT_SECONDS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "context_tokens": 2048,
            "output_tokens": 32,
            "retries": 0,
        },
    }
    output = Path(__file__).resolve().parents[1] / "runs/m02-local-smoke.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print(f"Saved synthetic-only report: {output}", flush=True)


if __name__ == "__main__":
    main()
