# Optional local Ollama setup

The offline commands and all unit tests work without Ollama. This guide is for
the optional real-model M02 entry point, ollama_adapter.py. It connects only to
http://127.0.0.1:11434/api/chat and requests qwen3:1.7b. No hosted-provider API key
or fallback is configured.

## Prerequisites and verified configuration

- Python 3.14.8 on Linux was used for the recorded tests.
- The recorded benign live round trip used Ollama 0.40.13 and qwen3:1.7b.
  This is a tested configuration, not a recommendation that this is the latest
  release. Model tags/templates and runtime behavior can change; compatibility
  with other versions has not been established by this repository.
- Install Ollama using its [official download/setup instructions](https://docs.ollama.com/quickstart).
  Model weights and the runtime are not included in this repository.
- The [qwen3:1.7b listing](https://ollama.com/library/qwen3:1.7b) provides the selected
  model. Download size is not the total RAM requirement. Local CPU inference can
  be slow; the client has finite request and generation limits.

## Start a local server

These environment-variable commands are for a POSIX shell such as bash or zsh.
Use a separately managed terminal and ensure another server is not already using
port 11434. If Ollama is managed by an app or service, apply the settings to that
process and restart it instead, as described in the official FAQ.

Terminal 1:

```sh
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve
```

OLLAMA_HOST sets the server binding. OLLAMA_NO_CLOUD=1 disables cloud features;
Ollama must be restarted for a changed setting to apply. See the official
[server configuration and local-only FAQ](https://docs.ollama.com/faq).

Terminal 2:

```sh
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3:1.7b
ollama --version
```

Pulling the model needs network access and disk space. This guide documents the
command; the Python client does not download a missing model automatically.
See the official [CLI reference](https://docs.ollama.com/cli).

## Run from the repository root

```sh
python3 scripts/check_local_model.py
python3 ollama_adapter.py
```

The first command checks text generation without executing a tool. The second
runs the welcome-only checked model/tool/result loop against in-memory synthetic
notes. It reports actual outcomes, usage when available and failure/stop reasons.
The file-backed M03 store has not yet been connected to that live entry point.

Generated JSON reports go under runs/, which is ignored by Git. The model may
answer without using a tool or return an unsupported response; those outcomes
are reported rather than treated as successful tool use. Live inference is not
part of the offline unit-test suite.

## Proposal inspection and replay

```sh
python3 scripts/inspect_tool_proposal.py
python3 replay_tool_proposal.py
```

The inspector makes one fresh model call, saves its reply under runs/ and invokes
no tool. The replay reads that saved reply and applies the checked dispatcher
without making a model call. Run the inspector first; the saved input is not
shipped with this repository. A replay requires a supported tool proposal; a
text-only reply is not one.

## Stop the server

Stop the server you started in Terminal 1 with Ctrl-C. The Python client does
not manage its lifetime. Requests use keep_alive=0 to request model unloading.
Runtime logs and generated reports are local artifacts, not committed inputs.

The setup commands above were checked against primary Ollama documentation on
2026-11-12. They were not executed as part of the clean-export offline check;
the previously observed live result is described in the M02 milestone document.
