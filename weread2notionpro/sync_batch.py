def select_sync_batch(book_ids, limit, run_number=0):
    """Select a rotating bounded batch and return deferred book IDs."""
    unique_book_ids = list(dict.fromkeys(book_ids))
    bounded_limit = max(limit, 0)
    if len(unique_book_ids) <= bounded_limit:
        return unique_book_ids, []
    if bounded_limit == 0:
        return [], unique_book_ids

    start = (max(run_number, 0) * bounded_limit) % len(unique_book_ids)
    rotated_book_ids = unique_book_ids[start:] + unique_book_ids[:start]
    selected = rotated_book_ids[:bounded_limit]
    selected_ids = set(selected)
    deferred = [
        book_id for book_id in unique_book_ids if book_id not in selected_ids
    ]
    return selected, deferred
