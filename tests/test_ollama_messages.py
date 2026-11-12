"""Offline checks for the first M02 message boundary and host effects."""

from copy import deepcopy
import unittest
from unittest.mock import Mock

from agent import Dispatcher, InvalidRequest, ReadPolicy, make_store
from ollama_messages import tool_request_from_message


def proposal(note_id="welcome", name="read_note"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "provider-controlled-id",
            "function": {
                "index": 0,
                "name": name,
                "arguments": {"note_id": note_id},
            },
        }],
    }


class ProposalBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        dispatcher = Dispatcher(self.store, ReadPolicy(frozenset({"welcome"})))
        self.dispatch = Mock(wraps=dispatcher.dispatch)

    def execute(self, message, call_id="host-1"):
        request = tool_request_from_message(message, call_id=call_id)
        return self.dispatch(request)

    def test_welcome_crosses_translation_and_checked_dispatch(self):
        original = proposal()
        preserved = deepcopy(original)
        result = self.execute(original)
        self.assertEqual(result, {
            "type": "tool_result", "call_id": "host-1", "ok": True,
            "value": "Welcome to the synthetic note lab.",
        })
        self.assertEqual(self.store.read_calls, ["welcome"])
        self.dispatch.assert_called_once()
        self.assertEqual(original, preserved)

    def test_translation_preserves_extra_arguments_for_host_rejection(self):
        message = proposal()
        message["tool_calls"][0]["function"]["arguments"]["approved"] = True
        result = self.execute(message)
        self.assertTrue(self.dispatch.call_args.args[0]["arguments"]["approved"])
        self.assertEqual(result["error"]["code"], "invalid_request")
        self.assertEqual(self.store.read_calls, [])

    def test_well_formed_private_request_is_denied_by_host_policy(self):
        result = self.execute(proposal("private-demo"))
        self.assertEqual(result["error"]["code"], "permission_denied")
        self.assertEqual(self.store.read_calls, [])

    def test_unknown_function_remains_subject_to_host_registry(self):
        result = self.execute(proposal(name="unregistered_tool"))
        self.assertEqual(result["error"]["code"], "unknown_tool")
        self.assertEqual(self.store.read_calls, [])

    def test_bad_envelopes_and_multiple_calls_never_reach_dispatch(self):
        malformed = [
            None, [], "read_note('welcome')", {},
            {"role": "user", "content": "", "tool_calls": proposal()["tool_calls"]},
            {"role": "assistant", "content": "read_note('welcome')"},
        ]
        for calls in (None, {}, [], [None], [{}], [proposal()["tool_calls"][0]] * 2):
            message = proposal()
            message["tool_calls"] = calls
            malformed.append(message)
        for function in (None, [], {}, {"name": "", "arguments": {}},
                         {"name": 7, "arguments": {}},
                         {"name": "read_note", "arguments": '{"note_id":"welcome"}'}):
            message = proposal()
            message["tool_calls"][0]["function"] = function
            malformed.append(message)
        message = proposal()
        message["content"] = None
        malformed.append(message)
        for message in malformed:
            with self.subTest(message=message):
                with self.assertRaises(InvalidRequest):
                    self.execute(message)
        self.dispatch.assert_not_called()
        self.assertEqual(self.store.read_calls, [])

    def test_host_correlation_id_must_be_valid(self):
        for call_id in (None, "", 7):
            with self.subTest(call_id=call_id):
                with self.assertRaises(InvalidRequest):
                    self.execute(proposal(), call_id=call_id)
        self.dispatch.assert_not_called()
        self.assertEqual(self.store.read_calls, [])


if __name__ == "__main__":
    unittest.main()
