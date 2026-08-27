import logging
import time

from weread2notionpro.weread_gateway import (
    GATEWAY_URL,
    SKILL_VERSION,
    WeReadApiError,
    WeReadApiRateLimitError,
    WeReadGateway,
)

logger = logging.getLogger(__name__)

NOTEBOOK_PAGE_SIZE = 300

__all__ = [
    "GATEWAY_URL",
    "NOTEBOOK_PAGE_SIZE",
    "SKILL_VERSION",
    "WeReadApi",
    "WeReadApiError",
    "WeReadApiRateLimitError",
]


class WeReadApi:
    """Normalized WeRead domain client backed by the official Agent API."""

    def __init__(self, api_key=None, session=None, gateway=None, sleep=None):
        if gateway is not None and (api_key is not None or session is not None):
            raise ValueError("提供 gateway 时不能同时提供 api_key 或 session")
        self.gateway = gateway or WeReadGateway(
            api_key=api_key,
            session=session,
            sleep=sleep,
        )
        self.sleep = sleep or getattr(self.gateway, "sleep", time.sleep)
        # Preserve these attributes for existing integrations and tests.
        self.api_key = self.gateway.api_key
        self.session = self.gateway.session
        self._book_urls = {}

    def _request(self, api_name, **params):
        return self.gateway.request(api_name, **params)

    def get_bookshelf(self):
        """获取完整电子书书架。"""
        data = self._request("/shelf/sync")
        books = data.get("books") or []
        self._book_urls.update(
            {
                book["bookId"]: book["deepLink"]
                for book in books
                if book.get("bookId") and book.get("deepLink")
            }
        )
        logger.info(f"获取到书架信息，包含 {len(books)} 本电子书")
        return data

    def get_notebooklist(self):
        """获取全部笔记本，兼容现有同步器需要的扁平书籍结构。"""
        notebooks = []
        last_sort = None
        seen_cursors = set()

        while True:
            params = {"count": NOTEBOOK_PAGE_SIZE}
            if last_sort is not None:
                params["lastSort"] = last_sort
            data = self._request("/user/notebooks", **params)
            page = data.get("books") or []
            notebooks.extend(page)

            if not data.get("hasMore") or not page:
                break

            next_cursor = page[-1].get("sort")
            if next_cursor is None or next_cursor in seen_cursors:
                raise WeReadApiError("微信读书笔记本分页游标无效，已停止以避免重复同步")
            seen_cursors.add(next_cursor)
            last_sort = next_cursor
            self.sleep(0.2)

        formatted_books = []
        for notebook in notebooks:
            book_info = (notebook.get("book") or {}).copy()
            book_id = notebook.get("bookId") or book_info.get("bookId")
            if not book_id:
                continue
            book_info.update(
                {
                    "bookId": book_id,
                    "noteCount": notebook.get("noteCount") or 0,
                    "reviewCount": notebook.get("reviewCount") or 0,
                    "bookmarkCount": notebook.get("bookmarkCount") or 0,
                    "readingProgress": notebook.get("readingProgress") or 0,
                    "markedStatus": notebook.get("markedStatus") or 0,
                    "sort": notebook.get("sort") or 0,
                }
            )
            if book_info.get("deepLink"):
                self._book_urls[book_id] = book_info["deepLink"]
            formatted_books.append(book_info)

        # 使用稳定顺序，具体批次优先级由同步层决定。
        formatted_books.sort(key=lambda book: book.get("sort") or 0)
        logger.info(f"获取到 {len(formatted_books)} 本有笔记的书")
        return formatted_books

    def get_bookinfo(self, bookId):
        data = self._request("/book/info", bookId=str(bookId))
        if data.get("deepLink"):
            self._book_urls[str(bookId)] = data["deepLink"]
        return data

    def get_bookmark_list(self, bookId):
        data = self._request("/book/bookmarklist", bookId=str(bookId))
        bookmarks = data.get("updated") or []
        logger.info(f"获取到 {len(bookmarks)} 个划线")
        return bookmarks

    def get_review_list(self, bookId):
        reviews = []
        synckey = 0
        seen_cursors = set()

        while True:
            data = self._request(
                "/review/list/mine",
                bookid=str(bookId),
                synckey=synckey,
                count=100,
            )
            page = data.get("reviews") or []
            for item in page:
                review = item.get("review", item)
                if not isinstance(review, dict):
                    continue
                if review.get("type") == 4:
                    review = {"chapterUid": 1000000, **review}
                reviews.append(review)

            if not data.get("hasMore"):
                break

            next_cursor = data.get("synckey")
            if next_cursor is None or next_cursor in seen_cursors:
                raise WeReadApiError("微信读书想法分页游标无效，已停止以避免重复同步")
            seen_cursors.add(next_cursor)
            synckey = next_cursor

        logger.info(f"获取到 {len(reviews)} 个想法")
        return reviews

    def get_chapter_info(self, bookId):
        data = self._request("/book/chapterinfo", bookId=str(bookId))
        chapters = data.get("chapters") or []
        if not chapters:
            logger.warning(f"书籍 {bookId} 未返回章节信息，保留现有章节数据")
            return None
        chapters.append(
            {
                "chapterUid": 1000000,
                "chapterIdx": 1000000,
                "updateTime": 1683825006,
                "readAhead": 0,
                "title": "点评",
                "level": 1,
            }
        )
        logger.info(f"获取到 {len(chapters)} 个章节信息")
        return {
            chapter["chapterUid"]: chapter
            for chapter in chapters
            if chapter.get("chapterUid") is not None
        }

    def get_read_info(self, bookId):
        data = self._request("/book/getprogress", bookId=str(bookId))
        book_data = data.get("book") or {}
        progress = book_data.get("progress", 0)
        reading_time = book_data.get("readingTime")
        if reading_time is None:
            reading_time = book_data.get("recordReadingTime", 0)
        read_days = data.get("readDays", book_data.get("readDays"))

        result = {
            "bookId": str(bookId),
            "timestamp": data.get("timestamp"),
            "readingTime": reading_time,
            "progress": progress,
            "readingProgress": progress,
            "markedStatus": (
                4 if progress >= 100 or book_data.get("finishTime") else 1
            ),
            "startReadingTime": book_data.get("startReadingTime"),
            "finishedDate": book_data.get("finishTime"),
            "finishTime": book_data.get("finishTime"),
            "lastReadingDate": book_data.get("updateTime"),
            "updateTime": book_data.get("updateTime"),
            "chapterUid": book_data.get("chapterUid"),
            "chapterOffset": book_data.get("chapterOffset"),
            "isStartReading": book_data.get("isStartReading"),
            "readDetail": data.get("readDetail", {}),
            "bookInfo": data.get("bookInfo", {}),
            "book": book_data,
        }
        if read_days:
            result["totalReadDay"] = read_days
        logger.info(
            f"获取阅读进度: bookId={bookId}, 进度={progress}%, "
            f"阅读时长={reading_time}秒"
        )
        return result

    def get_api_data(self):
        """获取本年度逐日阅读数据，供阅读热力图同步使用。"""
        data = self._request("/readdata/detail", mode="annually", baseTime=0)
        if data.get("dailyReadTimes") is not None:
            data["readTimes"] = data["dailyReadTimes"]
        else:
            data.setdefault("readTimes", {})
        return data

    def get_url(self, book_id):
        book_id = str(book_id)
        if book_id not in self._book_urls:
            info = self.get_bookinfo(book_id)
            if info.get("deepLink"):
                self._book_urls[book_id] = info["deepLink"]
        return self._book_urls.get(book_id)
