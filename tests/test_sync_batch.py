import unittest

from weread2notionpro.sync_batch import select_sync_batch


class SyncBatchTest(unittest.TestCase):
    def test_caps_and_defers_books_in_order(self):
        selected, deferred = select_sync_batch(
            ["book-1", "book-2", "book-2", "book-3", "book-4"],
            2,
        )

        self.assertEqual(selected, ["book-1", "book-2"])
        self.assertEqual(deferred, ["book-3", "book-4"])

    def test_rotates_deferred_books_across_runs(self):
        selected, deferred = select_sync_batch(
            ["book-1", "book-2", "book-3", "book-4"],
            2,
            run_number=1,
        )

        self.assertEqual(selected, ["book-3", "book-4"])
        self.assertEqual(deferred, ["book-1", "book-2"])


if __name__ == "__main__":
    unittest.main()
