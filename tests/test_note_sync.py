import unittest

from weread2notionpro.note_sync import (
    notebook_needs_sync,
    remote_collection_complete,
    select_notebooks_to_sync,
)


class NoteSyncTest(unittest.TestCase):
    def test_rejects_partial_remote_collection(self):
        self.assertFalse(remote_collection_complete(expected_count=3, actual_count=0))
        self.assertFalse(remote_collection_complete(expected_count=3, actual_count=2))
        self.assertTrue(remote_collection_complete(expected_count=3, actual_count=3))

    def test_count_change_requires_sync_even_when_sort_matches(self):
        notebook = {
            "bookId": "book-1",
            "sort": 100,
            "noteCount": 2,
            "reviewCount": 1,
            "bookmarkCount": 0,
        }
        notion_book = {
            "Sort": 100,
            "noteCount": 1,
            "reviewCount": 1,
            "bookmarkCount": 0,
        }

        self.assertTrue(notebook_needs_sync(notebook, notion_book))

    def test_matching_sort_and_counts_skips_sync(self):
        notebook = {
            "bookId": "book-1",
            "sort": 100,
            "noteCount": 2,
            "reviewCount": 1,
            "bookmarkCount": 0,
        }
        notion_book = {
            "Sort": 100,
            "noteCount": 2,
            "reviewCount": 1,
            "bookmarkCount": 0,
        }

        self.assertFalse(notebook_needs_sync(notebook, notion_book))

    def test_backfill_selects_newest_changed_notebooks(self):
        notebooks = [
            {"bookId": "old", "sort": 100, "noteCount": 1},
            {"bookId": "missing", "sort": 400, "noteCount": 1},
            {"bookId": "new", "sort": 300, "noteCount": 1},
            {"bookId": "middle", "sort": 200, "noteCount": 1},
        ]
        notion_books = {
            "old": {"Sort": 100},
            "new": {"Sort": 300},
            "middle": {"Sort": 200},
        }

        selected, deferred, unchanged, missing = select_notebooks_to_sync(
            notebooks,
            notion_books,
            limit=2,
        )

        self.assertEqual(
            [notebook["bookId"] for notebook in selected],
            ["new", "middle"],
        )
        self.assertEqual(
            [notebook["bookId"] for notebook in deferred],
            ["old"],
        )
        self.assertEqual(unchanged, [])
        self.assertEqual(
            [notebook["bookId"] for notebook in missing],
            ["missing"],
        )

    def test_null_sort_does_not_break_backfill_ordering(self):
        notebooks = [
            {"bookId": "unknown", "sort": None, "noteCount": 1},
            {"bookId": "recent", "sort": 200, "noteCount": 1},
        ]
        notion_books = {
            "unknown": {"Sort": None},
            "recent": {"Sort": 100},
        }

        selected, _, _, _ = select_notebooks_to_sync(
            notebooks,
            notion_books,
            limit=2,
        )

        self.assertEqual(
            [notebook["bookId"] for notebook in selected],
            ["recent", "unknown"],
        )


if __name__ == "__main__":
    unittest.main()
