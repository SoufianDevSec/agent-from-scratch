"""M02: bounded local chat -> checked note read -> tool result -> chat."""

from copy import deepcopy
from http.client import HTTPException
import json
from pathlib import Path
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from agent import Dispatcher, InvalidRequest, ReadPolicy, make_store, validate_request
from ollama_messages import tool_request_from_message


URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:1.7b"
MAX_TURNS = 4
TIMEOUT_SECONDS = 120
MAX_REQUEST_BYTES = 32768
MAX_RESPONSE_BYTES = 1024 * 1024
CONTEXT_TOKENS = 2048
OUTPUT_TOKENS = 128
DEFAULT_TASK = (
    "Please use read_note to read the note with note_id welcome, "
    "then tell me what the note says."
)
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


class ProtocolError(ValueError):
    """The response is outside the deliberately small supported protocol."""


class RequestLimitError(ValueError):
    """The serialized request exceeds the host's byte limit."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def encode_request(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > MAX_REQUEST_BYTES:
        raise RequestLimitError("Request exceeded the byte limit.")
    return body


def post_chat(payload: dict) -> dict:
    """One fixed-loopback POST, with no proxy or retry."""
    request = Request(
        URL, data=encode_request(payload),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    opener = build_opener(ProxyHandler({}), NoRedirects())
    with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProtocolError("Response exceeded the byte limit.")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise ProtocolError("Response was not valid JSON.") from error


def token_count(response: dict, field: str) -> int | None:
    value = response.get(field)
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ProtocolError(f"Invalid reported token count: {field}.")
    return value


def total_tokens(calls: list[dict], field: str) -> int | None:
    counts = [call[field] for call in calls]
    return None if any(count is None for count in counts) else sum(counts)


def run_live_agent(*, transport=None, max_turns=MAX_TURNS,
                   user_task=DEFAULT_TASK, on_progress=None) -> dict:
    """Keep provider history and host authority separate; fixtures inject transport."""
    if type(max_turns) is not int or not 1 <= max_turns <= MAX_TURNS:
        raise ValueError("max_turns must be an integer between one and four.")
    if not isinstance(user_task, str) or not user_task.strip():
        raise ValueError("user_task must be nonempty text.")
    send = post_chat if transport is None else transport
    store = make_store()
    policy = ReadPolicy(frozenset({"welcome"}))
    dispatcher = Dispatcher(store, policy)
    messages = [{"role": "user", "content": user_task}]
    calls = []
    tool_events = []
    pending_event = None
    dispatch_attempts = 0
    stop_reason = "turn_limit"
    error_message = None
    final_text = None
    started = time.perf_counter()

    for turn in range(1, max_turns + 1):
        payload = {
            "model": MODEL,
            "messages": deepcopy(messages),
            "tools": [deepcopy(READ_NOTE_TOOL)],
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "options": {"num_ctx": CONTEXT_TOKENS,
                        "num_predict": OUTPUT_TOKENS, "temperature": 0},
        }
        try:
            encode_request(payload)  # Apply the same preflight bound to fixtures.
        except RequestLimitError as error:
            stop_reason, error_message = "request_limit", str(error)
            break
        call = {
            "number": turn, "request": payload, "response": None,
            "reported_model": None, "done_reason": None,
            "input_tokens": None, "output_tokens": None,
            "elapsed_seconds": None, "error": None,
        }
        calls.append(call)  # A transport attempt counts even if it fails.
        if on_progress is not None:
            on_progress("request", {"number": turn, "payload": payload})
        call_started = time.perf_counter()
        try:
            response = send(deepcopy(payload))
            call["response"] = deepcopy(response)
        except (OSError, HTTPException) as error:
            stop_reason, error_message = "transport_error", str(error)
            call["error"] = error_message
            break
        except ValueError as error:
            stop_reason, error_message = "protocol_error", str(error)
            call["error"] = error_message
            break
        finally:
            call["elapsed_seconds"] = round(time.perf_counter() - call_started, 3)

        try:
            if not isinstance(response, dict):
                raise ProtocolError("Expected a response object.")
            call["reported_model"] = response.get("model")
            call["done_reason"] = response.get("done_reason")
            call["input_tokens"] = token_count(response, "prompt_eval_count")
            call["output_tokens"] = token_count(response, "eval_count")
            if response.get("done") is not True:
                raise ProtocolError("Expected a completed non-streaming response.")
            if response.get("model") != MODEL:
                raise ProtocolError("Response model differs from the requested model.")
            if response.get("done_reason") == "length":
                stop_reason = "output_limit"
                error_message = "This response hit its output limit; its proposal was not executed."
                call["error"] = error_message
                break
            if response.get("done_reason") != "stop":
                raise ProtocolError("Unsupported generation stop reason.")
            message = response.get("message")
            if (not isinstance(message, dict) or message.get("role") != "assistant"
                    or not isinstance(message.get("content"), str)):
                raise ProtocolError("Expected an assistant message with string content.")
            proposals = message.get("tool_calls", [])
            if not isinstance(proposals, list):
                raise ProtocolError("Expected a tool_calls list when present.")
            if pending_event is not None:
                # The saved request and completed reply establish this delivery.
                pending_event["returned_on_model_call"] = turn
                pending_event = None
            if on_progress is not None:
                on_progress("response", {"number": turn, "message": message})

            if not proposals:
                if not message["content"].strip():
                    raise ProtocolError("Empty assistant reply without a tool proposal.")
                messages.append(deepcopy(message))
                final_text = message["content"]
                stop_reason = "final"
                break

            # This installed Qwen template renders content OR calls, not both.
            if message["content"]:
                raise ProtocolError("Mixed text and tool proposals are unsupported.")
            request = tool_request_from_message(message, call_id=f"live-{turn}")
            validate_request(request)  # Do not dispatch malformed canonical calls.
            proposal = proposals[0]  # The converter has established exactly one.
            provider_id = proposal.get("id")
            if "id" in proposal and (not isinstance(provider_id, str) or not provider_id):
                raise ProtocolError("Expected a nonempty provider call ID when present.")
            function = proposal["function"]
            if "index" in function:
                index = function["index"]
                if type(index) is not int or index != 0:
                    raise ProtocolError("Expected index zero for the single proposal.")

            messages.append(deepcopy(message))
            dispatch_attempts += 1
            result = dispatcher.dispatch(request)
            tool_message = {
                "role": "tool", "tool_name": request["name"],
                "content": json.dumps(result),
            }
            if provider_id is not None:
                tool_message["tool_call_id"] = provider_id
            messages.append(tool_message)
            pending_event = {
                "model_call": turn, "request": request, "result": result,
                "tool_message": deepcopy(tool_message), "returned_on_model_call": None,
            }
            tool_events.append(pending_event)
            if on_progress is not None:
                on_progress("tool_result", pending_event)
        except (ProtocolError, InvalidRequest) as error:
            stop_reason, error_message = "protocol_error", str(error)
            call["error"] = error_message
            break

    round_trip_completed = any(
        event["result"]["ok"] and event["returned_on_model_call"] is not None
        for event in tool_events
    )
    return {
        "experiment": "live_checked_note_round_trip",
        "transport": "local_http" if transport is None else "injected_fixture",
        "requested_model": MODEL,
        "permission_check": "on", "allowed_note_ids": sorted(policy.allowed_note_ids),
        "stop_reason": stop_reason, "error": error_message, "final_text": final_text,
        "model_calls": len(calls), "dispatch_attempts": dispatch_attempts,
        "handler_calls": len(store.read_calls), "handler_reads": list(store.read_calls),
        "usage": {"input_tokens": total_tokens(calls, "input_tokens"),
                  "output_tokens": total_tokens(calls, "output_tokens")},
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "calls": calls, "tool_events": tool_events, "messages": messages,
        "round_trip_completed": round_trip_completed,
        "live_round_trip_completed": transport is None and round_trip_completed,
        "limits": {
            "max_model_calls": max_turns, "max_dispatches_per_response": 1,
            "socket_timeout_seconds": TIMEOUT_SECONDS,
            "follow_redirects": False, "http_proxy": False,
            "max_request_bytes": MAX_REQUEST_BYTES, "max_response_bytes": MAX_RESPONSE_BYTES,
            "context_tokens": CONTEXT_TOKENS, "output_tokens": OUTPUT_TOKENS, "retries": 0,
        },
    }


def print_progress(kind: str, value: dict) -> None:
    print(f"LIVE {kind.upper()}:", flush=True)
    print(json.dumps(value, indent=2), flush=True)


def main() -> None:
    report = run_live_agent(on_progress=print_progress)
    output = Path(__file__).resolve().parent / "runs/m02-live-round-trip.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    summary = {key: report[key] for key in (
        "stop_reason", "error", "final_text", "model_calls", "dispatch_attempts",
        "handler_calls", "handler_reads", "usage", "elapsed_seconds", "live_round_trip_completed",
    )}
    print("LIVE RUN SUMMARY:", flush=True)
    print(json.dumps(summary, indent=2), flush=True)
    print(f"Saved full request/response evidence: {output}", flush=True)
    if not report["live_round_trip_completed"] or report["stop_reason"] != "final":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
