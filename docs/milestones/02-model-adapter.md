# M02 — Connect one real language model

State: local text generation verified, 2026-11-02. A bounded text-only smoke client is
available at [scripts/check_local_model.py](../../scripts/check_local_model.py).
The bounded real-model adapter implementation completed its benign live round
trip on 2026-11-10. Executable host authorization remains the enforcement that
tool descriptions cannot replace. A completed local read and confirmed delivery
of its result are recorded separately.
On 2026-11-03, [the proposal inspector](../../scripts/inspect_tool_proposal.py)
observed a real `read_note` proposal for `welcome`, with zero tool executions.
On 2026-11-09 the first message translator and a checked replay of that saved
proposal were implemented. Replay returned the welcome value with one dispatch,
one handler invocation and zero new model requests; 18 offline tests passed
(12 existing M01, six new boundary tests). This is fresh host execution of saved
model output. On November 10 ollama_adapter.py obtained a fresh proposal, performed
one checked welcome read and returned the actual result in its second HTTP request.
Ollama then answered with the welcome text. Two calls took 39.501 s, with reported
usage 389 input / 44 output tokens; 32 offline tests passed. Full decoded exchanges
and host evidence are saved locally in ignored runs/m02-live-round-trip.json.
Prerequisite: [M01](01-tool-boundary.md). Standalone runtime instructions are in
[local Ollama setup](../local-ollama.md).

## Purpose and first example

M01's `ScriptedModel` returns responses written in advance. M02 replaces that
source with a real language model. The Python application continues to decide
which proposed actions may run. We keep the same synthetic notes and `read_note`.

For the first example, the user asks to read `welcome`:

1. Our application sends the conversation and a description of `read_note` to
   the local model runtime. This description is the tool schema: names and
   expected argument fields, not a Python function or a grant of permission.
2. The language model generates a reply. It may propose `read_note` with
   `note_id` equal to `welcome`, answer in text, or produce an unusable reply.
3. An adapter translates a supported reply into the existing internal message
   format. This adapter is ordinary Python code, distinct from the language model.
4. The host validates a proposed call, looks up its registered function, checks
   the welcome-only policy, and invokes the function only if allowed.
5. The application sends the tool result back in the next model request. A final
   reply or a host limit ends the loop.

Explain this complete example and the necessary Python before implementation.
Do not assume a real model will reliably choose the tool just because a schema
was sent. Keep model behavior and host permission evidence separate.

## Local setup

Start with Ollama running `qwen3:1.7b`. Ollama is the runtime; Qwen is the model.
Use the local chat API through standard-library HTTP and JSON support. No agent
framework, provider SDK, additional provider, or automatic fallback is needed.

The official model listing reports a 1.4 GB model file and tool support. This is
not total RAM usage or measured tool-call reliability. The local text-generation
smoke test passed; suitability for real tool use remains to be verified. Prefer
short context and one request at a time; measure behavior before expanding.

Keep the runtime bound to loopback (`127.0.0.1:11434`) and disable its cloud
features for this configuration. Downloading the model initially needs internet.
Local inference does not have a hosted-provider per-token bill; it still consumes
local resources. No paid provider fallback is authorized.

The manual local setup installed Ollama 0.40.13 and the model on 2026-11-02.
The archive digest matched the official release metadata, and runtime logs
confirmed loopback binding, cloud disabled and CPU selection. Version, resolved
model digest and the bounded text-only result are recorded locally. This does
not complete the model-to-tool integration or establish general performance.

## Small implementation boundary

- Add one Ollama adapter using `POST /api/chat` with `stream: false`, conversation
  messages, and the explicit `read_note` schema. Preserve the assistant's tool
  proposal and the matching tool result in subsequent conversation history.
- Normalize only supported response shapes. Host-created correlation IDs bind
  requests to results; they do not grant permission. Separate response metadata
  from tool arguments. Reject malformed or multiple-call replies before dispatch;
  do not silently execute the first of several calls or parse prose as commands.
- Retain host validation, registry and welcome-only authorization. Live mode uses
  the permission check. Existing scripted fixtures remain available offline.
- Keep four model turns and at most one dispatched request per response. Specify
  a finite request timeout, context size, output-token ceiling and maximum HTTP
  response size before the first live run. Use no hidden retries. Stop visibly on
  transport/protocol errors or exhausted limits.
- Record model identity, elapsed time, stop reason, model calls, dispatch attempts,
  handler invocations and reported input/output token counts per response and in
  aggregate. Missing counts stay unknown, including after a failed request.

The initial API exchange is an application transport to a fixed local runtime,
not a model-callable network-fetch tool. No filesystem, shell, retrieval, MCP or
attack catalogue is introduced in M02. Only synthetic values enter this experiment.

## Implemented path and current limits

ollama_adapter.py owns the bounded loop and fixed-loopback HTTP client. Its
post_chat returns the decoded fresh response; the loop extracts message, uses
ollama_messages.tool_request_from_message and validates the canonical request
before invoking the existing Dispatcher. The original assistant proposal remains
in native history. The actual result becomes a role=tool message with tool_name,
JSON string content and optional tool_call_id matching the provider ID. Host
live-N IDs remain separate. The next iteration constructs a new request from the
updated history; the model does not independently call back into Python.

The implementation uses four calls, one proposal per reply, 120-second socket
timeout, 32 KiB request, 1 MiB response, num_ctx=2048 and num_predict=128. No proxy,
redirect, retry, provider fallback or prose-command execution is supported. The
socket timeout is not a whole-process deadline. Start and stop the Ollama server
separately as described in the standalone setup guide. A final answer before any
tool use is recorded but does not satisfy
the live round-trip success condition. Unknown or failed-call usage is not zero.

The installed v0.40.13 Qwen Go template renders assistant content instead of calls
when both are nonempty. The bounded adapter rejects that mixed shape, along with
multiple/malformed/truncated replies. It preserves provider IDs in native history;
the active template itself renders tool-result content rather than those IDs.
The runtime context setting does not preflight actual conversation token counts.
Only the small observed conversation is verified here. A read can finish before a
later transport/request/turn failure; reports keep that effect even if delivery
of its result is unconfirmed. A late tool result can remain unsent at the turn cap.
The observed final reply quoted the returned value; general answer fidelity and
prompt-injection resistance have not been evaluated.

## Evidence and completion

Use injected transport fixtures for request/response translation, tool-result
round trips, malformed and multiple calls, timeout/transport failure, and limits
or missing usage where relevant. Retain the M01 deterministic security tests.
These fixtures test integration and controls, not real model behavior.

Run one bounded live benign example. Record the actual proposal, host decision,
tool result and handler record alongside usage and timing. A text-only answer,
bad arguments, timeout or failure to use the tool is an observed result, not a
successful tool-use demonstration. Do not make live output an exact-text test.
Implementation completion requires one supported real-model tool proposal to
pass through checked dispatch and its actual result to reach the next model
request. A failed smoke test leaves implementation pending.

Trace one actual round trip through model/runtime, response translator, host
authorization, handler and returned result. Explain why a tool description
cannot enforce permission, and inspect a timeout fixture after the local read.
This milestone neither demonstrates prompt injection nor
proves model quality, full runtime isolation or universal resistance to attacks.

## Primary sources

API/model sources checked 2026-11-01; installation sources checked 2026-11-02:

- [Qwen3 1.7B model listing](https://ollama.com/library/qwen3:1.7b): model file and
  advertised tool support. The tag may change; record the resolved digest at setup.
- [Ollama chat API](https://docs.ollama.com/api/chat): request/response protocol,
  generation options and reported usage fields.
- [Ollama tool calling](https://docs.ollama.com/capabilities/tool-calling): assistant
  proposals and subsequent tool-result messages.
- [Ollama FAQ](https://docs.ollama.com/faq): local binding, cloud-disable setting,
  context configuration and CPU/GPU residency inspection.
- [Manual Linux installation](https://docs.ollama.com/linux) and
  [v0.40.13 release assets](https://github.com/ollama/ollama/releases/expanded_assets/v0.40.13):
  official archive and reported SHA-256 used for the local setup.

Tool-history fields and completion handling rechecked 2026-11-10 against current
official docs and installed-version source:

- [Tool calling examples](https://docs.ollama.com/capabilities/tool-calling): original
  assistant proposal followed by tool-name/string-content result history.
- [v0.40.13 message types](https://github.com/ollama/ollama/blob/v0.40.13/api/types.go):
  optional provider call IDs, tool_call_id and tool_name.
- [v0.40.13 chat route](https://github.com/ollama/ollama/blob/v0.40.13/server/routes.go)
  and [generation handling](https://github.com/ollama/ollama/blob/v0.40.13/llm/server.go):
  non-streaming aggregation and done_reason=length versus normal stop.
