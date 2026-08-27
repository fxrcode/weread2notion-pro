import unittest
from unittest import mock

import requests

from weread2notionpro.weread_api import (
    GATEWAY_URL,
    SKILL_VERSION,
    WeReadApi,
    WeReadApiError,
    WeReadApiRateLimitError,
)
from weread2notionpro.weread_gateway import WeReadGateway


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data = data
        self.status_code = status_code
        self.headers = {}

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = [
            FakeResponse(*response)
            if isinstance(response, tuple)
            else FakeResponse(response)
            for response in responses
        ]
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.responses.pop(0)


class WeReadApiTest(unittest.TestCase):
    def test_gateway_rejects_zero_attempts(self):
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            WeReadGateway(api_key="wrk-test", max_attempts=0)

    def test_gateway_uses_api_key_and_flat_payload(self):
        session = FakeSession([{"books": []}])
        api = WeReadApi(api_key="wrk-test", session=session)

        api.get_bookshelf()

        self.assertEqual(
            session.headers["Authorization"],
            "Bearer wrk-test",
        )
        self.assertEqual(session.calls[0]["url"], GATEWAY_URL)
        self.assertEqual(
            session.calls[0]["json"],
            {"api_name": "/shelf/sync", "skill_version": SKILL_VERSION},
        )

    def test_notebooks_follow_last_sort_cursor(self):
        session = FakeSession(
            [
                {
                    "books": [
                        {
                            "bookId": "book-1",
                            "book": {"title": "第一本"},
                            "noteCount": 2,
                            "reviewCount": 1,
                            "bookmarkCount": 3,
                            "sort": 20,
                        }
                    ],
                    "hasMore": 1,
                },
                {
                    "books": [
                        {
                            "bookId": "book-2",
                            "book": {"title": "第二本"},
                            "noteCount": 4,
                            "sort": 10,
                        }
                    ],
                    "hasMore": 0,
                },
            ]
        )
        sleep = mock.Mock()
        api = WeReadApi(api_key="wrk-test", session=session, sleep=sleep)

        books = api.get_notebooklist()

        self.assertEqual([book["bookId"] for book in books], ["book-2", "book-1"])
        self.assertEqual(books[1]["bookmarkCount"], 3)
        self.assertEqual(session.calls[0]["json"]["count"], 300)
        self.assertEqual(session.calls[1]["json"]["lastSort"], 20)
        sleep.assert_called_once_with(0.2)

    def test_reviews_follow_synckey_cursor_and_unwrap_items(self):
        session = FakeSession(
            [
                {
                    "reviews": [{"review": {"reviewId": "r1", "type": 4}}],
                    "hasMore": 1,
                    "synckey": 123,
                },
                {
                    "reviews": [{"review": {"reviewId": "r2", "chapterUid": 2}}],
                    "hasMore": 0,
                    "synckey": 456,
                },
            ]
        )
        api = WeReadApi(api_key="wrk-test", session=session)

        reviews = api.get_review_list("book-1")

        self.assertEqual([review["reviewId"] for review in reviews], ["r1", "r2"])
        self.assertEqual(reviews[0]["chapterUid"], 1000000)
        self.assertEqual(session.calls[1]["json"]["synckey"], 123)

    def test_read_progress_maps_official_fields_to_sync_model(self):
        session = FakeSession(
            [
                {
                    "bookId": "book-1",
                    "book": {
                        "progress": 100,
                        "recordReadingTime": 3600,
                        "finishTime": 1700000000,
                        "updateTime": 1700000001,
                    },
                    "timestamp": 1700000002,
                }
            ]
        )
        api = WeReadApi(api_key="wrk-test", session=session)

        result = api.get_read_info("book-1")

        self.assertEqual(result["readingTime"], 3600)
        self.assertEqual(result["readingProgress"], 100)
        self.assertEqual(result["markedStatus"], 4)
        self.assertEqual(result["finishedDate"], 1700000000)

    def test_finish_time_preserves_finished_status(self):
        session = FakeSession(
            [
                {
                    "readDays": 0,
                    "book": {
                        "progress": 98,
                        "recordReadingTime": 3600,
                        "finishTime": 1700000000,
                    }
                }
            ]
        )
        api = WeReadApi(api_key="wrk-test", session=session)

        result = api.get_read_info("book-1")

        self.assertEqual(result["markedStatus"], 4)
        self.assertNotIn("totalReadDay", result)

    def test_reading_time_prefers_actual_time_over_record_time(self):
        session = FakeSession(
            [
                {
                    "readDays": 0,
                    "book": {
                        "progress": 41,
                        "readingTime": 744,
                        "recordReadingTime": 0,
                    }
                }
            ]
        )
        api = WeReadApi(api_key="wrk-test", session=session)

        result = api.get_read_info("book-1")

        self.assertEqual(result["readingTime"], 744)
        self.assertNotIn("totalReadDay", result)

    def test_empty_chapter_response_preserves_existing_chapters(self):
        session = FakeSession([{"chapters": []}])
        api = WeReadApi(api_key="wrk-test", session=session)

        self.assertIsNone(api.get_chapter_info("book-1"))

    def test_gateway_errors_are_not_treated_as_empty_data(self):
        session = FakeSession([{"errcode": 401, "errmsg": "invalid api key"}])
        api = WeReadApi(api_key="wrk-test", session=session)

        with self.assertRaisesRegex(WeReadApiError, "invalid api key"):
            api.get_bookmark_list("book-1")

    def test_transient_gateway_error_is_retried(self):
        session = FakeSession(
            [
                ({"errmsg": "busy"}, 499),
                {"updated": [{"bookmarkId": "bookmark-1"}]},
            ]
        )
        api = WeReadApi(api_key="wrk-test", session=session, sleep=mock.Mock())

        bookmarks = api.get_bookmark_list("book-1")

        self.assertEqual(bookmarks[0]["bookmarkId"], "bookmark-1")
        self.assertEqual(len(session.calls), 2)

    def test_transport_exception_is_retried(self):
        session = mock.Mock()
        session.headers = {}
        session.post.side_effect = [
            requests.ConnectionError("temporarily unavailable"),
            FakeResponse({"books": []}),
        ]
        sleep = mock.Mock()
        api = WeReadApi(api_key="wrk-test", session=session, sleep=sleep)

        api.get_bookshelf()

        self.assertEqual(session.post.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_non_json_transient_error_is_retried(self):
        session = FakeSession(
            [
                (ValueError("not json"), 499),
                {"updated": [{"bookmarkId": "bookmark-1"}]},
            ]
        )
        session.responses[0].headers["Retry-After"] = "Wed, 26 Aug 2026 08:00:00 GMT"
        sleep = mock.Mock()
        api = WeReadApi(
            api_key="wrk-test",
            session=session,
            sleep=sleep,
        )

        bookmarks = api.get_bookmark_list("book-1")

        self.assertEqual(bookmarks[0]["bookmarkId"], "bookmark-1")
        sleep.assert_called_once_with(60)

    def test_exhausted_rate_limit_has_distinct_error(self):
        session = FakeSession([({"errmsg": "busy"}, 499)] * 4)
        api = WeReadApi(api_key="wrk-test", session=session, sleep=mock.Mock())

        with self.assertRaises(WeReadApiRateLimitError):
            api.get_bookmark_list("book-1")

        self.assertEqual(len(session.calls), 4)

    def test_rate_limit_in_success_response_has_distinct_error(self):
        session = FakeSession([{"errcode": -1, "errmsg": "请求次数已用完"}])
        api = WeReadApi(api_key="wrk-test", session=session)

        with self.assertRaises(WeReadApiRateLimitError):
            api.get_bookmark_list("book-1")

    def test_empty_403_stops_as_rate_limit(self):
        session = FakeSession([(ValueError("empty body"), 403)])
        api = WeReadApi(api_key="wrk-test", session=session)

        with self.assertRaises(WeReadApiRateLimitError):
            api.get_bookmark_list("book-1")

        self.assertEqual(len(session.calls), 1)

    def test_json_403_stops_as_rate_limit(self):
        session = FakeSession([({"errmsg": "forbidden"}, 403)])
        api = WeReadApi(api_key="wrk-test", session=session)

        with self.assertRaises(WeReadApiRateLimitError):
            api.get_bookmark_list("book-1")

        self.assertEqual(len(session.calls), 1)

    def test_invalid_json_success_response_reports_payload_error(self):
        session = FakeSession([(ValueError("invalid"), 200)])
        api = WeReadApi(api_key="wrk-test", session=session)

        with self.assertRaisesRegex(
            WeReadApiError,
            "返回了无效JSON.*HTTP状态码: 200",
        ):
            api.get_bookmark_list("book-1")


if __name__ == "__main__":
    unittest.main()
