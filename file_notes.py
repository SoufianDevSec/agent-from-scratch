"""M03: checked reads of synthetic files in a static, host-controlled directory."""

import argparse
import json
from pathlib import Path

from agent import Dispatcher, NoteReadError, ReadPolicy


MAX_NOTE_BYTES = 4096
DEFAULT_ROOT = Path(__file__).resolve().parent / "lab-notes"
NOTE_FILES = {
    "welcome": "welcome.txt",
    "private-demo": "private-demo.txt",
}


class FileNoteStore:
    """Map host-owned IDs to bounded UTF-8 files; the tree must remain trusted."""

    def __init__(self, root: Path, note_files: dict[str, str]):
        try:
            resolved_root = Path(root).resolve(strict=True)
            if not resolved_root.is_dir():
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise ValueError("Note root must be an existing directory.") from None

        self.root = resolved_root
        self._note_files = dict(note_files)
        self.read_calls: list[str] = []
        self.file_events: list[dict] = []

    def read_note(self, note_id: str) -> str:
        # Invocation is not evidence that opening or reading succeeded.
        self.read_calls.append(note_id)
        event = {
            "note_id": note_id,
            "file": None,
            "opened": False,
            "bytes_returned": 0,
            "completed": False,
        }
        self.file_events.append(event)

        # IDs never become paths. An unknown ID raises KeyError for the dispatcher.
        filename = self._note_files[note_id]
        if not isinstance(filename, str) or not filename:
            raise NoteReadError("invalid_note_file", "Note file path is invalid.")

        try:
            relative = Path(filename)
            if relative.is_absolute():
                raise NoteReadError(
                    "invalid_note_file", "Note file must use a relative path."
                )

            target = (self.root / relative).resolve(strict=True)
            if not target.is_relative_to(self.root):
                raise NoteReadError(
                    "invalid_note_file", "Note file is outside the lab directory."
                )
            if not target.is_file():
                raise NoteReadError(
                    "invalid_note_file", "Note target must be a regular file."
                )

            # These checks assume the host-controlled tree does not change here.
            event["file"] = target.relative_to(self.root).as_posix()
            with target.open("rb") as source:
                event["opened"] = True
                data = source.read(MAX_NOTE_BYTES + 1)
                # Application bytes, not a count of physical filesystem I/O.
                event["bytes_returned"] = len(data)

            if len(data) > MAX_NOTE_BYTES:
                raise NoteReadError("note_too_large", "Note exceeds the byte limit.")
            text = data.decode("utf-8")
        except FileNotFoundError:
            raise NoteReadError("not_found", "Note file does not exist.") from None
        except UnicodeDecodeError:
            raise NoteReadError("invalid_encoding", "Note must contain UTF-8 text.") from None
        except ValueError:
            raise NoteReadError("invalid_note_file", "Note file path is invalid.") from None
        except (OSError, RuntimeError):
            raise NoteReadError("read_failed", "Note file could not be read.") from None

        event["completed"] = True
        return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--note-id", default="welcome")
    args = parser.parse_args()

    try:
        store = FileNoteStore(DEFAULT_ROOT, NOTE_FILES)
    except ValueError as error:
        parser.error(str(error))
    policy = ReadPolicy(frozenset({"welcome"}))
    request = {
        "type": "tool_call",
        "call_id": "file-1",
        "name": "read_note",
        "arguments": {"note_id": args.note_id},
    }
    result = Dispatcher(store, policy).dispatch(request)
    print(json.dumps({
        "experiment": "checked_file_note_read",
        "proposal_source": "command_line_fixture",
        "model_calls": 0,
        "permission_check": "on",
        "allowed_note_ids": sorted(policy.allowed_note_ids),
        "max_note_bytes": MAX_NOTE_BYTES,
        "request": request,
        "result": result,
        "handler_reads": store.read_calls,
        "file_events": store.file_events,
    }, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
