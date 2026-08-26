import logging
import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
log_filename = os.path.join(
    log_dir, f"weread_sync_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)
if not logger.handlers:
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_filename, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.propagate = False
    logger.info(f"日志文件已创建: {log_filename}")


GATEWAY_URL = "https://i.weread.qq.com/api/agent/gateway"
SKILL_VERSION = "1.0.4"


class WeReadApiError(RuntimeError):
    """Raised when the official WeRead gateway rejects a request."""


class WeReadApi:
    def __init__(self, api_key=None, session=None):
        self.api_key = api_key or os.getenv("WEREAD_API_KEY")
        if not self.api_key or not self.api_key.strip():
            raise WeReadApiError(
                "没有找到 WEREAD_API_KEY，请从 "
                "https://weread.qq.com/r/weread-skills 获取并配置"
            )

        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_key.strip()}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "weread2notion-pro",
            }
        )
        self._book_urls = {}

    @staticmethod
    def _retry_delay(response, attempt):
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 0)
            except (TypeError, ValueError):
                # HTTP-date 格式无法直接换算时使用保守冷却时间。
                return 60
        if response.status_code in (429, 499):
            # 官方网关目前按时间窗口限流且通常不返回 Retry-After。
            return 60
        return 2 ** (attempt + 1)

    def _request(self, api_name, **params):
        payload = {
            "api_name": api_name,
            "skill_version": SKILL_VERSION,
            **params,
        }
        logger.info(f"调用微信读书官方API: {api_name}")
        data = None
        for attempt in range(4):
            try:
                response = self.session.post(GATEWAY_URL, json=payload, timeout=30)
            except requests.RequestException as exc:
                if attempt < 3:
                    time.sleep(2**attempt)
                    continue
                raise WeReadApiError(
                    f"微信读书API请求失败 ({api_name}): {exc}"
                ) from exc

            try:
                data = response.json()
            except ValueError as exc:
                if (
                    response.status_code in (429, 499)
                    or response.status_code >= 500
                ) and attempt < 3:
                    delay = self._retry_delay(response, attempt)
                    logger.warning(
                        f"微信读书API暂时不可用 ({response.status_code})，"
                        f"{delay:g}秒后重试"
                    )
                    time.sleep(delay)
                    continue
                raise WeReadApiError(
                    f"微信读书API返回了无效JSON ({api_name})"
                ) from exc

            if response.status_code < 400:
                break

            if response.status_code in (429, 499) or response.status_code >= 500:
                if attempt < 3:
                    delay = self._retry_delay(response, attempt)
                    logger.warning(
                        f"微信读书API暂时不可用 ({response.status_code})，"
                        f"{delay:g}秒后重试"
                    )
                    time.sleep(delay)
                    continue

            errmsg = (
                data.get("errmsg", data.get("errMsg"))
                if isinstance(data, dict)
                else None
            )
            detail = f": {errmsg}" if errmsg else ""
            raise WeReadApiError(
                f"微信读书API HTTP错误 ({api_name}): "
                f"{response.status_code}{detail}"
            )
        else:
            raise WeReadApiError(f"微信读书API重试失败 ({api_name})")

        if not isinstance(data, dict):
            raise WeReadApiError(f"微信读书API返回格式异常 ({api_name})")

        upgrade_info = data.get("upgrade_info")
        if upgrade_info:
            message = (
                upgrade_info.get("message")
                if isinstance(upgrade_info, dict)
                else str(upgrade_info)
            )
            raise WeReadApiError(
                f"微信读书API要求升级 Skill（当前 {SKILL_VERSION}）: {message}"
            )

        errcode = data.get("errcode", data.get("errCode", 0))
        if errcode:
            errmsg = data.get("errmsg", data.get("errMsg", "未知错误"))
            raise WeReadApiError(
                f"微信读书API返回错误 ({api_name}): {errmsg} (错误码: {errcode})"
            )

        return data

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
            params = {"count": 100}
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
            time.sleep(0.2)

        formatted_books = []
        for notebook in notebooks:
            book_info = (notebook.get("book") or {}).copy()
            book_id = notebook.get("bookId") or book_info.get("bookId")
            if not book_id:
                continue
            book_info.update(
                {
                    "bookId": book_id,
                    "noteCount": notebook.get("noteCount", 0),
                    "reviewCount": notebook.get("reviewCount", 0),
                    "bookmarkCount": notebook.get("bookmarkCount", 0),
                    "readingProgress": notebook.get("readingProgress", 0),
                    "markedStatus": notebook.get("markedStatus", 0),
                    "sort": notebook.get("sort", 0),
                }
            )
            if book_info.get("deepLink"):
                self._book_urls[book_id] = book_info["deepLink"]
            formatted_books.append(book_info)

        # 先处理旧笔记；中途失败时不会越过尚未同步的较旧记录。
        formatted_books.sort(key=lambda book: book.get("sort", 0))
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
        reading_time = book_data.get(
            "recordReadingTime", book_data.get("readingTime", 0)
        )

        result = {
            "bookId": str(bookId),
            "timestamp": data.get("timestamp"),
            "readingTime": reading_time,
            "progress": progress,
            "readingProgress": progress,
            "markedStatus": (
                4 if progress >= 100 or book_data.get("finishTime") else 1
            ),
            "totalReadDay": data.get("readDays", 0),
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
