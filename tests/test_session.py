"""Tests for directory-scoped sessions (SessionManager)."""

import re
import tempfile
import unittest
from pathlib import Path

from mcp_client_for_ollama.utils.session import SessionManager


class TestSessionManager(unittest.TestCase):
    def setUp(self):
        # Redirect session storage to a throwaway dir for the whole test.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_base = SessionManager.BASE
        SessionManager.BASE = Path(self._tmp.name)
        self.addCleanup(lambda: setattr(SessionManager, "BASE", self._orig_base))
        self.cwd = "/home/peter/proj"

    def _sm(self):
        return SessionManager(cwd=self.cwd)

    def test_new_mints_8_hex_id(self):
        sid = self._sm().new()
        self.assertRegex(sid, re.compile(r"^[0-9a-f]{8}$"))

    def test_save_load_roundtrip(self):
        sm = self._sm()
        sm.new()
        history = [{"query": "hi", "response": "yo"}]
        self.assertTrue(sm.save(history, "llama3", "http://h:1"))

        data = self._sm().load(sm.id)
        self.assertIsNotNone(data)
        self.assertEqual(data["history"], history)
        self.assertEqual(data["model"], "llama3")
        self.assertEqual(data["host"], "http://h:1")
        self.assertEqual(data["cwd"], self.cwd)

    def test_save_is_atomic_and_preserves_created(self):
        sm = self._sm()
        sm.new()
        sm.save([{"query": "a", "response": "b"}], "m", "h")
        created = self._sm().load(sm.id)["created"]
        sm._created = created  # simulate same in-memory session
        sm.save([{"query": "a", "response": "b"}, {"query": "c", "response": "d"}], "m", "h")
        data = self._sm().load(sm.id)

        self.assertEqual(data["created"], created)            # created preserved
        self.assertGreaterEqual(data["updated"], created)     # updated stamped
        self.assertEqual(len(data["history"]), 2)
        # No temp file left behind.
        self.assertEqual(list(Path(self._tmp.name).rglob("*.tmp")), [])

    def test_resolve_last_picks_newest(self):
        self.assertIsNone(self._sm().resolve_last())  # empty dir

        a = self._sm(); a.new()
        a.save([], "m", "h")
        # Second session, saved later -> newer "updated".
        b = self._sm(); b.new()
        b.save([], "m", "h")

        self.assertEqual(self._sm().resolve_last(), b.id)

    def test_load_missing_and_malformed(self):
        sm = self._sm()
        self.assertIsNone(sm.load("deadbeef"))   # missing
        self.assertIsNone(sm.load(""))           # empty id

        # Malformed JSON in a real session path.
        sm.new()
        path = sm._path(sm.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(self._sm().load(sm.id))

    def test_load_rejects_bad_structure(self):
        sm = self._sm()
        sm.new()
        path = sm._path(sm.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"history": [{"query": "x"}]}', encoding="utf-8")  # missing response
        self.assertIsNone(self._sm().load(sm.id))

    def test_encode_cwd_stable(self):
        self.assertEqual(SessionManager.encode_cwd("/a/b/c"), "-a-b-c")


if __name__ == "__main__":
    unittest.main()
