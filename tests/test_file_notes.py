"""Real-file effects and controlled failures for the static M03 boundary."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import Dispatcher, ReadPolicy
import file_notes
from file_notes import DEFAULT_ROOT, MAX_NOTE_BYTES, NOTE_FILES, FileNoteStore


def request(note_id="welcome"):
    return {
        "type": "tool_call",
        "call_id": "file-test",
        "name": "read_note",
        "arguments": {"note_id": note_id},
    }


class FileNoteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root = self.parent / "notes"
        self.root.mkdir()
        (self.root / "welcome.txt").write_text("Hello from a real file.\n", encoding="utf-8")
        (self.root / "private-demo.txt").write_text(
            "DUMMY_SECRET_NOT_REAL", encoding="utf-8"
        )

    def make_store(self, mapping=None):
        return FileNoteStore(self.root, NOTE_FILES if mapping is None else mapping)

    def dispatch(self, store, note_id="welcome", allowed=None):
        policy = ReadPolicy(frozenset({"welcome"} if allowed is None else allowed))
        return Dispatcher(store, policy).dispatch(request(note_id))

    def assert_error(self, result, code):
        self.assertFalse(result["ok"])
        self.assertEqual(result["call_id"], "file-test")
        self.assertEqual(result["error"]["code"], code)
        self.assertNotIn("value", result)

    def assert_no_content_open(self, store, *, note_id="welcome", code):
        # These fixtures are static. This checks rejection before content open;
        # it does not claim safety against concurrent filesystem replacement.
        with patch.object(Path, "open", side_effect=AssertionError("Unexpected open")):
            result = self.dispatch(store, note_id, allowed={note_id})
        self.assert_error(result, code)
        self.assertEqual(store.read_calls, [note_id])
        self.assertEqual(len(store.file_events), 1)
        self.assertFalse(store.file_events[0]["opened"])
        self.assertFalse(store.file_events[0]["completed"])
        self.assertEqual(store.file_events[0]["bytes_returned"], 0)

    def test_allowed_read_returns_current_file_text_and_completed_evidence(self):
        store = self.make_store()
        for text in ("Hello from a real file.\n", "Changed on disk: café.\n"):
            with self.subTest(text=text):
                (self.root / "welcome.txt").write_text(text, encoding="utf-8")
                result = self.dispatch(store)
                self.assertTrue(result["ok"])
                self.assertEqual(result["value"], text)
                self.assertEqual(store.file_events[-1], {
                    "note_id": "welcome", "file": "welcome.txt", "opened": True,
                    "bytes_returned": len(text.encode("utf-8")), "completed": True,
                })
        self.assertEqual(store.read_calls, ["welcome", "welcome"])
        self.assertEqual(len(store.file_events), 2)

    def test_private_file_inside_root_is_denied_before_handler(self):
        store = self.make_store()
        with patch.object(Path, "open", side_effect=AssertionError("Unexpected open")):
            result = self.dispatch(store, "private-demo")
        self.assert_error(result, "permission_denied")
        self.assertEqual(store.read_calls, [])
        self.assertEqual(store.file_events, [])
        self.assertNotIn("DUMMY_SECRET_NOT_REAL", json.dumps(result))

    def test_host_can_authorize_private_id_without_changing_file_boundary(self):
        store = self.make_store()
        result = self.dispatch(store, "private-demo", allowed={"private-demo"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], "DUMMY_SECRET_NOT_REAL")
        self.assertEqual(store.read_calls, ["private-demo"])
        self.assertTrue(store.file_events[0]["completed"])

    def test_unknown_and_path_looking_ids_are_not_interpreted_as_paths(self):
        outside = self.parent / "outside.txt"
        outside.write_text("DUMMY_OUTSIDE_CONTENT", encoding="utf-8")
        for note_id in ("missing-id", "welcome.txt", "../outside.txt", str(outside)):
            with self.subTest(note_id=note_id):
                store = self.make_store()
                self.assert_no_content_open(store, note_id=note_id, code="not_found")
                self.assertIsNone(store.file_events[0]["file"])

    def test_mapping_is_copied_and_nested_in_root_files_are_supported(self):
        nested = self.root / "subfolder"
        nested.mkdir()
        (nested / "nested.txt").write_text("Nested lab text", encoding="utf-8")
        mapping = {"welcome": "subfolder/nested.txt"}
        store = self.make_store(mapping)
        mapping["welcome"] = "private-demo.txt"
        result = self.dispatch(store)
        self.assertEqual(result["value"], "Nested lab text")
        self.assertEqual(store.file_events[0]["file"], "subfolder/nested.txt")

    def test_configured_relative_escapes_and_common_prefix_sibling_are_rejected(self):
        outside = self.parent / "outside.txt"
        outside.write_text("DUMMY_OUTSIDE_CONTENT", encoding="utf-8")
        sibling = self.parent / "notes-extra"
        sibling.mkdir()
        (sibling / "other.txt").write_text("DUMMY_SIBLING_CONTENT", encoding="utf-8")
        for configured in ("../outside.txt", "../notes-extra/other.txt"):
            with self.subTest(configured=configured):
                store = self.make_store({"welcome": configured})
                self.assert_no_content_open(store, code="invalid_note_file")

    def test_static_symlink_to_outside_target_is_rejected(self):
        outside = self.parent / "outside.txt"
        outside.write_text("DUMMY_OUTSIDE_CONTENT", encoding="utf-8")
        link = self.root / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable in this test environment: {type(error).__name__}")
        store = self.make_store({"welcome": "linked.txt"})
        self.assert_no_content_open(store, code="invalid_note_file")

    def test_absolute_configured_filename_is_rejected_even_when_inside_root(self):
        store = self.make_store({"welcome": str(self.root / "welcome.txt")})
        self.assert_no_content_open(store, code="invalid_note_file")

    def test_invalid_configured_path_values_are_controlled_errors(self):
        for configured in ("", None, 17, "bad\x00name.txt"):
            with self.subTest(configured=configured):
                store = self.make_store({"welcome": configured})
                self.assert_no_content_open(store, code="invalid_note_file")

    def test_directory_and_static_fifo_are_rejected_without_open(self):
        directory = self.root / "folder"
        directory.mkdir()
        store = self.make_store({"welcome": "folder"})
        self.assert_no_content_open(store, code="invalid_note_file")
        if hasattr(os, "mkfifo"):
            fifo = self.root / "pipe"
            try:
                os.mkfifo(fifo)
            except (OSError, NotImplementedError):
                return  # Directory case remains checked on platforms without FIFOs.
            store = self.make_store({"welcome": "pipe"})
            self.assert_no_content_open(store, code="invalid_note_file")

    def test_missing_allowed_file_is_not_found_and_was_not_opened(self):
        store = self.make_store({"welcome": "missing.txt"})
        self.assert_no_content_open(store, code="not_found")

    def test_nonexistent_or_nondirectory_roots_fail_at_configuration_time(self):
        for root in (self.parent / "missing-root", self.root / "welcome.txt"):
            with self.subTest(root=root):
                with self.assertRaises(ValueError) as raised:
                    FileNoteStore(root, NOTE_FILES)
                self.assertNotIn(str(self.parent), str(raised.exception))

    def test_byte_cap_and_strict_utf8_have_honest_access_evidence(self):
        self.assertEqual(MAX_NOTE_BYTES, 4096)
        cases = (
            (b"a" * MAX_NOTE_BYTES, None, "a" * MAX_NOTE_BYTES),
            (b"a" * (MAX_NOTE_BYTES + 1), "note_too_large", None),
            ("é".encode("utf-8") * (MAX_NOTE_BYTES // 2), None,
             "é" * (MAX_NOTE_BYTES // 2)),
            ("é".encode("utf-8") * (MAX_NOTE_BYTES // 2) + b"a",
             "note_too_large", None),
            (b"\xff", "invalid_encoding", None),
            (b"", None, ""),
        )
        for raw, error_code, expected_text in cases:
            with self.subTest(byte_count=len(raw), error_code=error_code):
                (self.root / "welcome.txt").write_bytes(raw)
                store = self.make_store()
                result = self.dispatch(store)
                if error_code:
                    self.assert_error(result, error_code)
                else:
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["value"], expected_text)
                self.assertEqual(store.read_calls, ["welcome"])
                event = store.file_events[0]
                self.assertTrue(event["opened"])
                self.assertEqual(event["bytes_returned"], len(raw))
                self.assertEqual(event["completed"], error_code is None)

    def test_content_read_is_binary_and_bounded_before_size_rejection(self):
        (self.root / "welcome.txt").write_bytes(b"a" * (MAX_NOTE_BYTES * 3))
        original_open = Path.open
        read_limits = []
        modes = []

        class TrackedReader:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def read(self, size=-1):
                read_limits.append(size)
                return self.handle.read(size)

        def tracked_open(path, *args, **kwargs):
            modes.append(args[0] if args else kwargs.get("mode", "r"))
            return TrackedReader(original_open(path, *args, **kwargs))

        store = self.make_store()
        with patch.object(Path, "open", autospec=True, side_effect=tracked_open):
            result = self.dispatch(store)
        self.assert_error(result, "note_too_large")
        self.assertEqual(modes, ["rb"])
        self.assertEqual(read_limits, [MAX_NOTE_BYTES + 1])
        self.assertEqual(store.file_events[0]["bytes_returned"], MAX_NOTE_BYTES + 1)
        self.assertFalse(store.file_events[0]["completed"])

    def test_operating_system_error_is_sanitized(self):
        store = self.make_store()
        detail = "DUMMY_HOST_PATH /not-a-real-home/private.txt: access denied"
        with patch.object(Path, "open", side_effect=PermissionError(detail)):
            result = self.dispatch(store)
        self.assert_error(result, "read_failed")
        serialized = json.dumps(result)
        self.assertNotIn(detail, serialized)
        self.assertNotIn("DUMMY_HOST_PATH", serialized)
        self.assertNotIn(str(self.parent), serialized)
        self.assertEqual(store.read_calls, ["welcome"])
        self.assertFalse(store.file_events[0]["opened"])
        self.assertFalse(store.file_events[0]["completed"])
        self.assertEqual(store.file_events[0]["bytes_returned"], 0)

    def test_default_root_is_project_anchored_independently_of_working_directory(self):
        self.assertEqual(
            DEFAULT_ROOT, Path(file_notes.__file__).resolve().parent / "lab-notes"
        )
        expected = (DEFAULT_ROOT / NOTE_FILES["welcome"]).read_text(encoding="utf-8")
        previous = Path.cwd()
        try:
            os.chdir(self.parent)
            result = self.dispatch(FileNoteStore(DEFAULT_ROOT, NOTE_FILES))
        finally:
            os.chdir(previous)
        self.assertEqual(result["value"], expected)


if __name__ == "__main__":
    unittest.main()
