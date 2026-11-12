"""Offline, effect-based checks for the bounded M02 model loop."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import ollama_adapter as adapter
from ollama_adapter import MAX_REQUEST_BYTES, MODEL, run_live_agent


def proposal(note_id="welcome", name="read_note", provider_id="provider-call-1"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": provider_id,
            "function": {
                "index": 0,
                "name": name,
                "arguments": {"note_id": note_id},
            },
        }],
    }


def response(message, *, input_tokens=13, output_tokens=5, **changes):
    result = {
        "model": MODEL,
        "done": True,
        "done_reason": "stop",
        "message": message,
        "prompt_eval_count": input_tokens,
        "eval_count": output_tokens,
    }
    result.update(changes)
    return result


def final(text="Finished.", **usage):
    return response({"role": "assistant", "content": text}, **usage)


class FixtureTransport:
    """Record request snapshots and reject any unplanned model attempt."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, payload):
        self.requests.append(deepcopy(payload))
        if len(self.requests) > len(self.responses):
            raise AssertionError("The loop made an unexpected transport call.")
        item = self.responses[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)


class LiveLoopTests(unittest.TestCase):
    def assert_no_tool_effects(self, report):
        self.assertEqual(report["dispatch_attempts"], 0)
        self.assertEqual(report["handler_calls"], 0)
        self.assertEqual(report["handler_reads"], [])
        self.assertEqual(report["tool_events"], [])

    def test_welcome_round_trip_preserves_history_actual_result_and_ids(self):
        original = proposal()
        transport = FixtureTransport([
            response(original),
            final("The welcome note was returned.", input_tokens=21, output_tokens=7),
        ])
        report = run_live_agent(transport=transport, user_task="Read welcome.")

        self.assertEqual(report["stop_reason"], "final")
        self.assertEqual(report["final_text"], "The welcome note was returned.")
        self.assertEqual(report["transport"], "injected_fixture")
        self.assertTrue(report["round_trip_completed"])
        self.assertFalse(report["live_round_trip_completed"])
        self.assertEqual(report["model_calls"], 2)
        self.assertEqual(report["dispatch_attempts"], 1)
        self.assertEqual(report["handler_calls"], 1)
        self.assertEqual(report["handler_reads"], ["welcome"])
        self.assertEqual(report["usage"], {"input_tokens": 34, "output_tokens": 12})
        self.assertEqual(len(transport.requests), 2)

        first, second = transport.requests
        self.assertEqual(first["model"], MODEL)
        self.assertFalse(first["stream"])
        self.assertEqual(first["messages"][-1], {
            "role": "user", "content": "Read welcome.",
        })
        self.assertEqual(first["tools"][0]["function"]["name"], "read_note")
        self.assertNotIn("DUMMY_SECRET_NOT_REAL", json.dumps(first))

        event = report["tool_events"][0]
        expected_result = {
            "type": "tool_result", "call_id": "live-1", "ok": True,
            "value": "Welcome to the synthetic note lab.",
        }
        self.assertEqual(event["model_call"], 1)
        self.assertEqual(event["request"]["call_id"], "live-1")
        self.assertEqual(event["result"], expected_result)
        self.assertEqual(event["returned_on_model_call"], 2)
        tool_message = event["tool_message"]
        self.assertEqual(tool_message["role"], "tool")
        self.assertEqual(tool_message["tool_name"], "read_note")
        self.assertEqual(tool_message["tool_call_id"], "provider-call-1")
        self.assertEqual(json.loads(tool_message["content"]), expected_result)
        self.assertEqual(second["messages"], first["messages"] + [original, tool_message])
        self.assertEqual(report["messages"][-1], {
            "role": "assistant", "content": "The welcome note was returned.",
        })
        self.assertEqual(report["calls"][0]["request"], first)
        self.assertEqual(report["calls"][0]["response"], response(original))
        self.assertEqual(report["calls"][0]["reported_model"], MODEL)

    def test_denied_private_and_unknown_tool_errors_return_without_reads(self):
        cases = [
            (proposal("private-demo"), "permission_denied"),
            (proposal(name="unregistered_tool"), "unknown_tool"),
        ]
        for message, code in cases:
            with self.subTest(code=code):
                transport = FixtureTransport([
                    response(message), final("I read the private note."),
                ])
                report = run_live_agent(transport=transport)
                self.assertEqual(report["stop_reason"], "final")
                self.assertEqual(report["dispatch_attempts"], 1)
                self.assertEqual(report["handler_calls"], 0)
                self.assertEqual(report["handler_reads"], [])
                event = report["tool_events"][0]
                self.assertFalse(event["result"]["ok"])
                self.assertEqual(event["result"]["error"]["code"], code)
                self.assertEqual(event["returned_on_model_call"], 2)
                self.assertEqual(
                    json.loads(transport.requests[1]["messages"][-1]["content"]),
                    event["result"],
                )
                self.assertNotIn("DUMMY_SECRET_NOT_REAL", json.dumps(report))

    def test_provider_metadata_is_optional_and_never_replaces_host_id(self):
        message = proposal()
        del message["tool_calls"][0]["id"]
        del message["tool_calls"][0]["function"]["index"]
        transport = FixtureTransport([response(message), final()])
        report = run_live_agent(transport=transport)
        self.assertEqual(report["stop_reason"], "final")
        event = report["tool_events"][0]
        self.assertEqual(event["request"]["call_id"], "live-1")
        self.assertEqual(event["result"]["call_id"], "live-1")
        self.assertNotIn("tool_call_id", event["tool_message"])
        self.assertEqual(transport.requests[1]["messages"][-2], message)

    def test_malformed_multiple_and_mixed_proposals_stop_before_dispatch(self):
        messages = []
        for calls in (None, {}, [None], [{}], proposal()["tool_calls"] * 2):
            message = proposal()
            message["tool_calls"] = calls
            messages.append(message)
        for arguments in (None, [], '{"note_id":"welcome"}', {},
                          {"note_id": True},
                          {"note_id": "welcome", "approved": True}):
            message = proposal()
            message["tool_calls"][0]["function"]["arguments"] = arguments
            messages.append(message)
        for provider_id in (None, "", 7):
            message = proposal(provider_id=provider_id)
            messages.append(message)
        for index in (True, 1, -1, "0", None):
            message = proposal()
            message["tool_calls"][0]["function"]["index"] = index
            messages.append(message)
        mixed = proposal()
        mixed["content"] = "I will read the note now."
        messages.append(mixed)

        for message in messages:
            with self.subTest(message=message):
                transport = FixtureTransport([response(message)])
                report = run_live_agent(transport=transport)
                self.assertEqual(report["stop_reason"], "protocol_error")
                self.assertEqual(report["model_calls"], 1)
                self.assertEqual(len(transport.requests), 1)
                self.assert_no_tool_effects(report)

    def test_invalid_api_envelopes_and_empty_finals_stop_without_effects(self):
        cases = [
            None, [], {},
            response(proposal(), done=False),
            response(proposal(), model="some-other-model"),
            response(proposal(), done_reason="unexpected"),
            response({"role": "user", "content": "hello"}),
            response({"role": "assistant", "content": None}),
            response({"role": "assistant", "content": ""}),
            response({"role": "assistant", "content": "   ", "tool_calls": []}),
        ]
        missing_done = response(proposal())
        del missing_done["done"]
        cases.append(missing_done)
        missing_reason = response(proposal())
        del missing_reason["done_reason"]
        cases.append(missing_reason)
        for field in ("prompt_eval_count", "eval_count"):
            for count in (True, -1, "3"):
                cases.append(response(proposal(), **{field: count}))
        for item in cases:
            with self.subTest(response=item):
                transport = FixtureTransport([item])
                report = run_live_agent(transport=transport)
                self.assertEqual(report["stop_reason"], "protocol_error")
                self.assertEqual(report["model_calls"], 1)
                self.assert_no_tool_effects(report)

    def test_output_limit_never_dispatches_a_complete_looking_proposal(self):
        transport = FixtureTransport([response(proposal(), done_reason="length")])
        report = run_live_agent(transport=transport)
        self.assertEqual(report["stop_reason"], "output_limit")
        self.assertEqual(report["model_calls"], 1)
        self.assert_no_tool_effects(report)

    def test_transport_failure_counts_attempt_without_retry(self):
        for error in (TimeoutError("timed out"), OSError("connection unavailable")):
            with self.subTest(error=error):
                transport = FixtureTransport([error])
                report = run_live_agent(transport=transport)
                self.assertEqual(report["stop_reason"], "transport_error")
                self.assertEqual(report["model_calls"], 1)
                self.assertEqual(len(transport.requests), 1)
                self.assertEqual(len(report["calls"]), 1)
                self.assertTrue(report["calls"][0]["error"])
                self.assertEqual(report["usage"], {
                    "input_tokens": None, "output_tokens": None,
                })
                self.assert_no_tool_effects(report)

    def test_second_transport_failure_preserves_effect_and_prior_usage(self):
        transport = FixtureTransport([
            response(proposal(), input_tokens=9, output_tokens=3),
            TimeoutError("second request timed out"),
        ])
        report = run_live_agent(transport=transport)
        self.assertEqual(report["stop_reason"], "transport_error")
        self.assertEqual((report["model_calls"], report["dispatch_attempts"]), (2, 1))
        self.assertEqual(report["handler_reads"], ["welcome"])
        self.assertEqual(report["calls"][0]["input_tokens"], 9)
        self.assertEqual(report["calls"][0]["output_tokens"], 3)
        self.assertIsNone(report["calls"][1]["input_tokens"])
        self.assertIsNone(report["calls"][1]["output_tokens"])
        self.assertEqual(report["usage"], {"input_tokens": None, "output_tokens": None})
        self.assertIsNone(report["tool_events"][0]["returned_on_model_call"])
        self.assertEqual(len(transport.requests), 2)

    def test_repeated_proposals_stop_at_exact_turn_cap(self):
        for limit in (1, 4):
            with self.subTest(max_turns=limit):
                transport = FixtureTransport([response(proposal())] * limit)
                report = run_live_agent(transport=transport, max_turns=limit)
                self.assertEqual(report["stop_reason"], "turn_limit")
                self.assertEqual(report["model_calls"], limit)
                self.assertEqual(report["dispatch_attempts"], limit)
                self.assertEqual(report["handler_calls"], limit)
                self.assertEqual(report["handler_reads"], ["welcome"] * limit)
                self.assertEqual(len(transport.requests), limit)
                self.assertEqual(
                    [event["request"]["call_id"] for event in report["tool_events"]],
                    [f"live-{turn}" for turn in range(1, limit + 1)],
                )
                self.assertEqual(
                    [event["returned_on_model_call"] for event in report["tool_events"]],
                    list(range(2, limit + 1)) + [None],
                )

    def test_missing_usage_stays_unknown_while_reported_zero_is_zero(self):
        unknown_input = final(input_tokens=0, output_tokens=3)
        del unknown_input["prompt_eval_count"]
        unknown_output = final(input_tokens=4, output_tokens=0)
        del unknown_output["eval_count"]
        cases = [
            ([final(input_tokens=0, output_tokens=0)], {"input_tokens": 0, "output_tokens": 0}),
            ([unknown_input], {"input_tokens": None, "output_tokens": 3}),
            ([unknown_output], {"input_tokens": 4, "output_tokens": None}),
            ([response(proposal(), input_tokens=0, output_tokens=0), unknown_input],
             {"input_tokens": None, "output_tokens": 3}),
            ([response(proposal(), input_tokens=0, output_tokens=0), unknown_output],
             {"input_tokens": 4, "output_tokens": None}),
        ]
        for responses, expected in cases:
            with self.subTest(expected=expected, turns=len(responses)):
                transport = FixtureTransport(responses)
                report = run_live_agent(transport=transport)
                self.assertEqual(report["stop_reason"], "final")
                self.assertEqual(report["usage"], expected)

    def test_invalid_turn_limits_are_rejected_before_transport(self):
        for limit in (0, -1, 5, True, 1.5, "2"):
            with self.subTest(limit=limit):
                transport = FixtureTransport([])
                with self.assertRaises(ValueError):
                    run_live_agent(transport=transport, max_turns=limit)
                self.assertEqual(transport.requests, [])

    def test_initial_request_limit_stops_before_any_transport_attempt(self):
        transport = FixtureTransport([])
        report = run_live_agent(transport=transport, user_task="x" * MAX_REQUEST_BYTES)
        self.assertEqual(report["stop_reason"], "request_limit")
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["calls"], [])
        self.assertEqual(transport.requests, [])
        self.assert_no_tool_effects(report)

    def test_history_growth_can_stop_before_sending_the_tool_result(self):
        message = proposal(provider_id="x" * MAX_REQUEST_BYTES)
        transport = FixtureTransport([response(message)])
        report = run_live_agent(transport=transport)
        self.assertEqual(report["stop_reason"], "request_limit")
        self.assertEqual(report["model_calls"], 1)
        self.assertEqual(report["handler_reads"], ["welcome"])
        self.assertEqual(len(transport.requests), 1)
        self.assertIsNone(report["tool_events"][0]["returned_on_model_call"])

    def test_http_transport_is_local_and_enforces_byte_and_timeout_limits(self):
        payload = {"model": MODEL, "messages": []}
        expected = final()
        cases = [
            (json.dumps(expected).encode("utf-8"), False),
            (b"not JSON", True),
            (b"x" * (adapter.MAX_RESPONSE_BYTES + 1), True),
        ]
        for raw, should_reject in cases:
            with self.subTest(response_bytes=len(raw), should_reject=should_reject):
                with patch("ollama_adapter.build_opener") as build:
                    opener = build.return_value
                    http_response = opener.open.return_value.__enter__.return_value
                    http_response.read.return_value = raw
                    if should_reject:
                        with self.assertRaises(adapter.ProtocolError):
                            adapter.post_chat(payload)
                    else:
                        self.assertEqual(adapter.post_chat(payload), expected)
                    self.assertEqual(build.call_args.args[0].proxies, {})
                    redirects = build.call_args.args[1]
                    self.assertIsInstance(redirects, adapter.NoRedirects)
                    self.assertIsNone(redirects.redirect_request(
                        None, None, 302, "Found", {}, "https://example.invalid/",
                    ))
                    opener.open.assert_called_once()
                    request = opener.open.call_args.args[0]
                    self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
                    self.assertEqual(request.get_method(), "POST")
                    self.assertEqual(json.loads(request.data), payload)
                    self.assertEqual(opener.open.call_args.kwargs, {
                        "timeout": adapter.TIMEOUT_SECONDS,
                    })
                    http_response.read.assert_called_once_with(adapter.MAX_RESPONSE_BYTES + 1)

        with patch("ollama_adapter.build_opener") as build:
            with self.assertRaises(adapter.RequestLimitError):
                adapter.post_chat({"content": "x" * MAX_REQUEST_BYTES})
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
