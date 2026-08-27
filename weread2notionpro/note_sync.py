NOTE_COUNT_FIELDS = ("noteCount", "reviewCount", "bookmarkCount")


def remote_collection_complete(expected_count, actual_count):
    """Reject partial endpoint results before deleting existing Notion data."""
    return expected_count <= actual_count


def notebook_needs_sync(notebook, notion_book):
    """Return whether remote notebook content differs from persisted state."""
    if notebook.get("sort") != notion_book.get("Sort"):
        return True
    return any(
        notebook.get(field, 0) != notion_book.get(field)
        for field in NOTE_COUNT_FIELDS
    )


def select_notebooks_to_sync(notebooks, notion_books, limit):
    """Select the newest changed notebooks within a quota-safe batch."""
    missing = []
    changed = []
    unchanged = []

    for notebook in notebooks:
        book_id = notebook.get("bookId")
        notion_book = notion_books.get(book_id)
        if notion_book is None:
            missing.append(notebook)
        elif notebook_needs_sync(notebook, notion_book):
            changed.append(notebook)
        else:
            unchanged.append(notebook)

    changed.sort(key=lambda notebook: notebook.get("sort") or 0, reverse=True)
    bounded_limit = max(limit, 0)
    return (
        changed[:bounded_limit],
        changed[bounded_limit:],
        unchanged,
        missing,
    )
