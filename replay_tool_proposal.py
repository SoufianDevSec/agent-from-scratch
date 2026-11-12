"""Replay the saved M02 proposal through checked dispatch; no model request."""

import json
from pathlib import Path

from agent import Dispatcher, ReadPolicy, make_store
from ollama_messages import tool_request_from_message


def main() -> None:
    project = Path(__file__).resolve().parent
    source = project / "runs/m02-tool-proposal.json"
    saved = json.loads(source.read_text())
    saved_message = saved["message"]

    request = tool_request_from_message(saved_message, call_id="replay-1")
    store = make_store()
    policy = ReadPolicy(frozenset({"welcome"}))
    dispatcher = Dispatcher(store, policy)
    result = dispatcher.dispatch(request)

    report = {
        "experiment": "saved_model_proposal_checked_replay",
        "source": source.name,
        "source_reported_model": saved.get("reported_model"),
        "permission_check": "on",
        "allowed_note_ids": sorted(policy.allowed_note_ids),
        "original_message": saved_message,
        "normalized_request": request,
        "tool_result": result,
        "model_calls_this_run": 0,
        "dispatch_attempts": 1,
        "handler_calls": len(store.read_calls),
        "handler_reads": store.read_calls,
        "result_sent_to_model": False,
    }
    output = project / "runs/m02-tool-replay.json"
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
