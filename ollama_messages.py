"""First M02 adapter piece: translate one structured proposal; execute nothing."""

from copy import deepcopy

from agent import InvalidRequest


def tool_request_from_message(message: object, call_id: str) -> dict:
    """Check the provider envelope and preserve arguments for host validation.

    The caller supplies the internal correlation ID. Provider IDs and indexes
    remain in the original message; neither grants permission. Final text replies
    and conversation/result translation are outside this first adapter piece.
    """
    if not isinstance(call_id, str) or not call_id:
        raise InvalidRequest("The host must supply a nonempty call_id.")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise InvalidRequest("Expected an assistant message.")
    if not isinstance(message.get("content"), str):
        raise InvalidRequest("Expected string assistant content.")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise InvalidRequest("Expected exactly one structured tool proposal.")
    call = calls[0]
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        raise InvalidRequest("Expected a function object in the tool proposal.")
    function = call["function"]
    if not isinstance(function.get("name"), str) or not function["name"]:
        raise InvalidRequest("Expected a nonempty function name.")
    if not isinstance(function.get("arguments"), dict):
        raise InvalidRequest("Expected an arguments object, not encoded text.")
    return {
        "type": "tool_call",
        "call_id": call_id,
        "name": function["name"],
        "arguments": deepcopy(function["arguments"]),
    }
