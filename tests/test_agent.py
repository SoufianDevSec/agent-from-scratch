"""Effect-based checks for the M01 synthetic tool boundary."""

import json
import unittest

from agent import Dispatcher, NoteStore, ReadPolicy, ScriptedModel, make_store, run_agent


def request(note_id="welcome", **changes):
    message = {
        "type": "tool_call",
        "call_id": "test-call",
        "name": "read_note",
        "arguments": {"note_id": note_id},
    }
    message.update(changes)
    return message


def dispatcher(store, checked):
    policy = ReadPolicy(frozenset({"welcome"})) if checked else None
    return Dispatcher(store, policy)


class PermissionTests(unittest.TestCase):
    def test_benign_read_keeps_working_in_both_modes(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                store = make_store()
                result = dispatcher(store, checked).dispatch(request())
                self.assertEqual(result, {
                    "type": "tool_result", "call_id": "test-call", "ok": True,
                    "value": "Welcome to the synthetic note lab.",
                })
                self.assertEqual(store.read_calls, ["welcome"])

    def test_forbidden_read_leaks_only_without_policy(self):
        permissive_store = make_store()
        leaked = dispatcher(permissive_store, False).dispatch(request("private-demo"))
        self.assertTrue(leaked["ok"])
        self.assertEqual(leaked["value"], "DUMMY_SECRET_NOT_REAL")
        self.assertEqual(permissive_store.read_calls, ["private-demo"])

        checked_store = make_store()
        blocked = dispatcher(checked_store, True).dispatch(request("private-demo"))
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["error"]["code"], "permission_denied")
        self.assertEqual(checked_store.read_calls, [])
        self.assertNotIn("DUMMY_SECRET_NOT_REAL", json.dumps(blocked))

    def test_extra_approval_cannot_supply_authorization(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                store = make_store()
                message = request(arguments={"note_id": "private-demo", "approved": True})
                result = dispatcher(store, checked).dispatch(message)
                self.assertEqual(result["error"]["code"], "invalid_request")
                self.assertEqual(store.read_calls, [])

    def test_unknown_tool_never_invokes_handler(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                store = make_store()
                result = dispatcher(store, checked).dispatch(request(name="unknown"))
                self.assertEqual(result["error"]["code"], "unknown_tool")
                self.assertEqual(store.read_calls, [])

    def test_malformed_requests_fail_before_handler(self):
        malformed = [
            None, [], "read_note", {},
            request(type="other"), request(call_id=""), request(call_id=3),
            request(name=None), request(name=""),
            request(arguments=[]), request(arguments=None), request(arguments={}),
            request(arguments={"note_id": "welcome", "extra": "field"}),
            request(""), request(None), request(7), request(True), request(["welcome"]),
            request(approved=True),
        ]
        missing_field = request()
        del missing_field["call_id"]
        malformed.append(missing_field)
        for checked in (False, True):
            for message in malformed:
                with self.subTest(checked=checked, message=message):
                    store = make_store()
                    result = dispatcher(store, checked).dispatch(message)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"]["code"], "invalid_request")
                    self.assertEqual(store.read_calls, [])

    def test_missing_allowed_note_records_attempt_but_no_success(self):
        store = NoteStore({})
        result = dispatcher(store, True).dispatch(request())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "not_found")
        self.assertEqual(store.read_calls, ["welcome"])

    def test_host_can_explicitly_authorize_a_different_note(self):
        store = make_store()
        host_policy = ReadPolicy(frozenset({"private-demo"}))
        result = Dispatcher(store, host_policy).dispatch(request("private-demo"))
        self.assertEqual(result["value"], "DUMMY_SECRET_NOT_REAL")
        self.assertEqual(store.read_calls, ["private-demo"])


class LoopTests(unittest.TestCase):
    def test_private_marker_enters_model_input_only_after_successful_read(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                model = ScriptedModel([
                    request("private-demo"),
                    {"type": "final", "content": "I read the private note."},
                ])
                store = make_store()
                result = run_agent(model, dispatcher(store, checked))
                self.assertEqual(result.stop_reason, "final")
                self.assertEqual((result.model_calls, result.dispatches), (2, 1))
                self.assertEqual(len(model.inputs), 2)
                self.assertNotIn("DUMMY_SECRET_NOT_REAL", json.dumps(model.inputs[0]))
                self.assertEqual(model.inputs[1][-1]["call_id"], "test-call")
                self.assertEqual(
                    "DUMMY_SECRET_NOT_REAL" in json.dumps(model.inputs[1]), not checked
                )
                self.assertEqual(
                    "DUMMY_SECRET_NOT_REAL" in json.dumps(result.messages), not checked
                )
                self.assertEqual(store.read_calls, [] if checked else ["private-demo"])

    def test_final_claim_alone_does_not_read_any_note(self):
        model = ScriptedModel([{"type": "final", "content": "I read the private note."}])
        store = make_store()
        result = run_agent(model, dispatcher(store, True))
        self.assertEqual(result.stop_reason, "final")
        self.assertEqual((result.model_calls, result.dispatches), (1, 0))
        self.assertEqual(store.read_calls, [])

    def test_repeated_calls_stop_at_exact_turn_limit(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                model = ScriptedModel([request()], repeat_last=True)
                store = make_store()
                result = run_agent(model, dispatcher(store, checked), max_turns=3)
                self.assertEqual(result.stop_reason, "turn_limit")
                self.assertEqual((result.model_calls, result.dispatches), (3, 3))
                self.assertEqual(len(model.inputs), 3)
                self.assertEqual(store.read_calls, ["welcome"] * 3)
                self.assertEqual(len(result.messages), 7)  # User + three call/result pairs.

    def test_malformed_output_is_returned_as_error_then_loop_can_finish(self):
        model = ScriptedModel([None, {"type": "final", "content": "Finished."}])
        store = make_store()
        result = run_agent(model, dispatcher(store, True))
        self.assertEqual(result.stop_reason, "final")
        self.assertEqual(result.messages[2]["error"]["code"], "invalid_request")
        self.assertEqual(store.read_calls, [])

    def test_invalid_limits_are_rejected_without_adapter_or_handler_calls(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit):
                model = ScriptedModel([request()], repeat_last=True)
                store = make_store()
                with self.assertRaises(ValueError):
                    run_agent(model, dispatcher(store, True), max_turns=limit)
                self.assertEqual(model.inputs, [])
                self.assertEqual(store.read_calls, [])


if __name__ == "__main__":
    unittest.main()
