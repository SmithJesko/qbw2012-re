"""The exporter must never overwrite its input or a non-SQLite file."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from export11 import check_output  # noqa: E402


class OutputSafetyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.qbw = os.path.join(self.dir, "books.qbw")
        with open(self.qbw, "wb") as f:
            f.write(b"\0" * 4096)

    def path(self, name):
        return os.path.join(self.dir, name)

    def test_new_sqlite_path_is_fine(self):
        self.assertIsNone(check_output(self.path("books.sqlite"), self.qbw))

    def test_previous_export_may_be_replaced(self):
        out = self.path("old.sqlite")
        sqlite3.connect(out).execute("CREATE TABLE t (x)")
        self.assertIsNone(check_output(out, self.qbw))

    def test_refuses_quickbooks_names(self):
        for name in ("other.qbw", "BOOKS.QBW", "backup.qbb", "log.tlg"):
            self.assertIsNotNone(check_output(self.path(name), self.qbw), name)

    def test_refuses_its_own_input(self):
        self.assertIsNotNone(check_output(self.qbw, self.qbw))
        renamed = self.path("same.sqlite")
        os.link(self.qbw, renamed)  # another name for the same file
        self.assertIsNotNone(check_output(renamed, self.qbw))

    def test_refuses_other_existing_files(self):
        out = self.path("notes.sqlite")
        with open(out, "w") as f:
            f.write("not a database")
        self.assertIsNotNone(check_output(out, self.qbw))


if __name__ == "__main__":
    unittest.main()
