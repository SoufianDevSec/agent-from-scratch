# M03 — Read real note files within a defined boundary

State: first checked offline file-backed unit implemented and verified 2026-11-11.
The layout and CLI below now exist. Live-loop storage selection is pending.
Prerequisite: [M02](02-model-adapter.md).

## Purpose and first example

The current NoteStore reads text from a Python dictionary. This milestone adds
access to real text files in a dedicated synthetic lab directory. A fixed file
read needs no language model; retaining the existing model/tool loop lets us
inspect how an untrusted proposal reaches a real operating-system resource.

Keep read_note(note_id), its schema and the host's welcome-only ReadPolicy.
Host configuration maps note IDs to filenames. This deliberately limits what
the model can select; it does not provide a general-purpose file browser.

Implemented layout, relative to agent-from-scratch:

```text
lab-notes/
    welcome.txt          # synthetic welcome text
    private-demo.txt     # DUMMY_SECRET_NOT_REAL
file_notes.py            # file-backed store and small checked demonstration
tests/test_file_notes.py # actual temporary-file boundary checks
```

Host-owned mapping:

```python
NOTE_FILES = {
    "welcome": "welcome.txt",
    "private-demo": "private-demo.txt",
}
```

A filename identifies a file inside a directory; a path describes its location.
Trace one behavior before implementation:

1. The model proposes read_note(note_id="welcome").
2. The existing dispatcher validates the proposal and checks the allowed-ID set.
3. The new store maps welcome to welcome.txt under the configured lab directory.
4. Store code checks the resolved location and file type, opens the file, reads
   a bounded amount and decodes accepted UTF-8 text.
5. The existing structured tool result contains the actual file contents.
6. The existing model loop can send that result back in its next request.

Permission to read a note and keeping its backing file within the lab directory
are separate rules. private-demo.txt is inside the directory but its ID remains
forbidden under welcome-only policy. Directory containment alone grants no read.

## Contract for the small implementation

- Add one FileNoteStore with the existing read_note interface and a copied,
  host-owned ID-to-relative-filename map. Keep the in-memory fixtures available.
- Anchor the default directory to the project location, not the process's
  current working directory. Treat directory structure and mapping as controlled
  fixtures; note text and model proposals remain untrusted inputs.
- Authorize the ID before invoking the store. Unknown IDs are not interpreted
  as paths. Do not concatenate a model-supplied ID into a filesystem path.
- Reject absolute configured filenames. Resolve the root and candidate target;
  compare filesystem path components, not string prefixes. Reject resolved
  targets outside the root and targets that are not regular files.
- Permit at most 4096 bytes of returned file content. Read in binary mode with
  a 4097-byte probe; reject oversize content rather than silently truncating it.
  Decode accepted content strictly as UTF-8. Do not read the entire file first
  and only then measure its size. Counts describe bytes returned to application
  code; buffered I/O may fetch extra data underneath, so do not claim an exact
  operating-system read count.
- Return controlled structured errors for missing/invalid targets, excessive
  size, invalid encoding and read failures. Raw OS errors/full host paths must
  not be passed to the language model as error messages.
- Show handler invocation separately from actual file opening, bytes examined
  and completed reads. A rejected oversize file may have been opened and probed;
  do not label every rejected request as zero file access. Pair successful-read
  records with the actual returned value.
- Preserve the checked dispatcher and M02 transport/history/limits. Connect a
  file-backed store to the existing loop through a small explicit storage choice;
  show that change before implementing it. Do not duplicate the agent loop.

## Implementation units and evidence

The tiny fixture/store and checked offline demonstration are implemented.
Explain Path objects, path joining, opening/closing, bounded reads, bytes/text
and errors in the actual code, grouping syntax into the complete read behavior.
Verified commands from the project directory:

```sh
python3 file_notes.py --note-id welcome
python3 file_notes.py --note-id private-demo
python3 -m unittest discover -s tests -v
```

Fresh November 11 runs: welcome returned actual text with its final newline,
one handler invocation, one opened file, 35 bytes returned and completed=true.
private-demo was denied with zero handler invocations/file events. Model calls
were zero. Success exits 0; a controlled tool error/denial exits 1. Bad root
configuration produces a fixed CLI error and exit 2. All 48 tests passed,
including 16 new real-file methods. A separate controlled bad-root check found
no traceback or host path in its error message.

Explain the actual output and file-access evidence before exposing storage selection to the existing
bounded live path, visibly previewing source/command changes before any live run.

Meaningful checks use actual synthetic temporary files:

- Allowed read returns those file contents; changing the file changes the value.
- Forbidden private ID is denied before its handler runs, although the file is
  inside the root. Unknown/path-looking IDs do not cause content access.
- Bad configured mappings escaping the root, including a sibling with a common
  string prefix, and a static link pointing outside are rejected.
- Missing files, directories, other nonregular targets and controlled read
  failures produce structured errors. Avoid assuming chmod alone proves denial
  when the test process may have privileges.
- Exactly 4096 bytes succeeds; 4097 bytes and invalid UTF-8 fail. Record bounded
  probing honestly. Existing M01/M02 regression tests continue passing.

Containment cases test configuration/fixture boundaries here. A model proposal
such as note_id="../outside.txt" is an unknown ID, not a reproduced model-driven
path traversal exploit. No prompt-injection claim follows from these tests.

## Trust model and limits

This is an application-level boundary over a static, host-controlled fixture
tree. Resolution, checking and opening are separate operations. The design does
not claim safety against hostile concurrent changes, malicious hard links,
compromised configuration/code or an attacker controlling the Python process.
An in-root link to another note can undermine ID-to-file expectations when that
tree is not trusted. OS/container isolation is a later milestone. Do not use
personal documents, real credentials, shell execution, network-fetch tools,
retrieval or MCP as part of this change.

## Completion

Record actual commands, file effects, errors and limits. After tracing a complete
read, explain the distinction between ID authorization and filesystem access.
Passing these checks demonstrates the stated static-fixture behavior, not
containment under hostile concurrent filesystem changes.

## Primary references

Python locally reports 3.14.8. Matching-version docs checked November 11:

- [Path resolution, containment and file queries](https://docs.python.org/3.14/library/pathlib.html):
  resolution handles links and parent components; is_relative_to alone is lexical.
- [Bounded binary reads](https://docs.python.org/3.14/library/io.html#io.BufferedIOBase.read):
  use a byte-count limit before decoding text.
