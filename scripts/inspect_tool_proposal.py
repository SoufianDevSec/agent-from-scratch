"""Show one real model tool proposal without running any proposed tool."""

import json
from pathlib import Path
import time
from urllib.request import ProxyHandler, Request, build_opener

from check_local_model import MAX_RESPONSE_BYTES, MODEL, TIMEOUT_SECONDS, URL


READ_NOTE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_note",
        "description": "Read one synthetic note by its note_id.",
        "parameters": {
            "type": "object",
            "properties": {"note_id": {"type": "string"}},
            "required": ["note_id"],
            "additionalProperties": False,
        },
    },
}


def main():
    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": "Please use read_note to read the note with note_id welcome.",
        }],
        "tools": [READ_NOTE_TOOL],
        "stream": False,
        "think": False,
        "keep_alive": 0,
        "options": {"num_ctx": 2048, "num_predict": 128, "temperature": 0},
    }
    print("REQUEST PREPARED FOR LOCAL OLLAMA:", flush=True)
    print(json.dumps(payload, indent=2), flush=True)
    request = Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = build_opener(ProxyHandler({}))
    started = time.perf_counter()
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Local response exceeded the byte limit.")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("done") is not True:
        raise ValueError("Expected one completed response object.")
    message = result.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ValueError("Expected an assistant message.")
    # Preserve the entire returned message, including all proposed calls.
    # Inspection neither authorizes calls nor executes the first returned call.
    report = {
        "experiment": "real_tool_proposal_inspection_only",
        "request": payload,
        "reported_model": result.get("model"),
        "message": message,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "done_reason": result.get("done_reason"),
        "input_tokens": result.get("prompt_eval_count"),
        "output_tokens": result.get("eval_count"),
        "model_calls": 1,
        "dispatch_attempts": 0,
        "handler_calls": 0,
        "limits": {
            "socket_timeout_seconds": TIMEOUT_SECONDS,
            "max_response_bytes": MAX_RESPONSE_BYTES,
            "context_tokens": 2048,
            "output_tokens": 128,
            "retries": 0,
        },
    }
    output = Path(__file__).resolve().parents[1] / "runs/m02-tool-proposal.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print("ACTUAL MODEL RESPONSE (NO TOOL EXECUTED):", flush=True)
    print(json.dumps(report, indent=2), flush=True)
    print(f"Saved inspection report: {output}", flush=True)


if __name__ == "__main__":
    main()
