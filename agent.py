"""M01: scripted tool requests against an in-memory permission boundary."""

import argparse
from copy import deepcopy
from dataclasses import dataclass
import json
from typing import Protocol


@dataclass(frozen=True)
class ToolRequest:
    call_id: str
    name: str
    note_id: str


class InvalidRequest(Exception):
    pass


class NoteReadError(Exception):
    """A controlled storage failure that can become a tool error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class NoteReader(Protocol):
    """Both storage implementations provide this method; this grants no access."""

    def read_note(self, note_id: str) -> str: ...


def validate_request(message: object) -> ToolRequest:
    """Validate shape only. This does not grant permission to read a note."""
    fields = {"type", "call_id", "name", "arguments"}
    if not isinstance(message, dict) or set(message) != fields:
        raise InvalidRequest("Expected exactly type, call_id, name, arguments.")
    if message["type"] != "tool_call":
        raise InvalidRequest("Expected a tool_call message.")
    for field in ("call_id", "name"):
        if not isinstance(message[field], str) or not message[field]:
            raise InvalidRequest(f"{field} must be a nonempty string.")
    arguments = message["arguments"]
    if not isinstance(arguments, dict) or set(arguments) != {"note_id"}:
        raise InvalidRequest("Expected exactly one argument: note_id.")
    if not isinstance(arguments["note_id"], str) or not arguments["note_id"]:
        raise InvalidRequest("note_id must be a nonempty string.")
    return ToolRequest(message["call_id"], message["name"], arguments["note_id"])


class NoteStore:
    """Synthetic storage plus a record of actual handler invocations."""

    def __init__(self, notes: dict[str, str]):
        self._notes = dict(notes)
        self.read_calls: list[str] = []

    def read_note(self, note_id: str) -> str:
        self.read_calls.append(note_id)
        return self._notes[note_id]


@dataclass(frozen=True)
class ReadPolicy:
    allowed_note_ids: frozenset[str]

    def allows(self, request: ToolRequest) -> bool:
        return request.note_id in self.allowed_note_ids


def tool_error(call_id: str | None, code: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "call_id": call_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


class Dispatcher:
    def __init__(self, store: NoteReader, policy: ReadPolicy | None):
        # The host chooses the registry and policy. None deliberately omits authz.
        self._handlers = {"read_note": store.read_note}
        self._policy = policy

    def dispatch(self, message: object) -> dict:
        try:
            request = validate_request(message)
        except InvalidRequest as error:
            call_id = message.get("call_id") if isinstance(message, dict) else None
            if not isinstance(call_id, str) or not call_id:
                call_id = None
            return tool_error(call_id, "invalid_request", str(error))

        handler = self._handlers.get(request.name)
        if handler is None:
            return tool_error(request.call_id, "unknown_tool", "Tool is not registered.")

        # Security boundary: reject before invoking any note handler.
        if self._policy is not None and not self._policy.allows(request):
            return tool_error(request.call_id, "permission_denied", "Note is not allowed.")

        try:
            value = handler(request.note_id)
        except KeyError:
            return tool_error(request.call_id, "not_found", "Note does not exist.")
        except NoteReadError as error:
            return tool_error(request.call_id, error.code, str(error))
        return {
            "type": "tool_result",
            "call_id": request.call_id,
            "ok": True,
            "value": value,
        }


class ScriptedModel:
    """Returns fixture messages; records exactly what the adapter received."""

    def __init__(self, responses: list[object], *, repeat_last: bool = False):
        if not responses:
            raise ValueError("At least one scripted response is required.")
        self._responses = deepcopy(responses)
        self._repeat_last = repeat_last
        self.inputs: list[list[object]] = []

    def respond(self, messages: list[object]) -> object:
        index = len(self.inputs)
        self.inputs.append(deepcopy(messages))
        if self._repeat_last:
            index = min(index, len(self._responses) - 1)
        return deepcopy(self._responses[index])


@dataclass
class RunResult:
    messages: list[object]
    stop_reason: str
    model_calls: int
    dispatches: int


def run_agent(
    model: ScriptedModel, dispatcher: Dispatcher, *, max_turns: int = 4
) -> RunResult:
    """One adapter response per turn, with at most one dispatch per response."""
    if type(max_turns) is not int or max_turns < 1:
        raise ValueError("max_turns must be a positive integer.")
    messages: list[object] = [
        {"type": "user", "content": "Read the requested synthetic lab note."}
    ]
    dispatches = 0
    for turn in range(1, max_turns + 1):
        message = model.respond(messages)
        messages.append(message)
        if (
            isinstance(message, dict)
            and set(message) == {"type", "content"}
            and message["type"] == "final"
            and isinstance(message["content"], str)
        ):
            return RunResult(messages, "final", turn, dispatches)
        result = dispatcher.dispatch(message)
        dispatches += 1
        messages.append(result)
    return RunResult(messages, "turn_limit", max_turns, dispatches)


def make_store() -> NoteStore:
    # Values live in host storage, outside the initial model-visible messages.
    return NoteStore({
        "welcome": "Welcome to the synthetic note lab.",
        "private-demo": "DUMMY_SECRET_NOT_REAL",
    })


def scenario_model(scenario: str) -> ScriptedModel:
    request = {
        "type": "tool_call",
        "call_id": "call-1",
        "name": "read_note",
        "arguments": {"note_id": "welcome" if scenario == "welcome" else "private-demo"},
    }
    if scenario == "claimed-approval":
        request["arguments"]["approved"] = True
    elif scenario == "unknown-tool":
        request["name"] = "unregistered_tool"
    elif scenario == "malformed":
        request["arguments"] = ["private-demo"]
    if scenario == "loop":
        request["arguments"] = {"note_id": "welcome"}
        return ScriptedModel([request], repeat_last=True)
    return ScriptedModel([
        request,
        {"type": "final", "content": "Script finished; inspect the tool result for evidence."},
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("both", "permissive", "checked"), default="both")
    parser.add_argument(
        "--scenario",
        choices=("welcome", "private", "claimed-approval", "unknown-tool", "malformed", "loop"),
        default="private",
    )
    args = parser.parse_args()
    modes = ("permissive", "checked") if args.mode == "both" else (args.mode,)
    reports = []
    for mode in modes:
        store = make_store()
        policy = ReadPolicy(frozenset({"welcome"})) if mode == "checked" else None
        result = run_agent(scenario_model(args.scenario), Dispatcher(store, policy))
        reports.append({
            "mode": mode,
            "scenario": args.scenario,
            "stop_reason": result.stop_reason,
            "model_calls": result.model_calls,
            "dispatches": result.dispatches,
            "handler_reads": store.read_calls,
            "messages": result.messages,
        })
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
