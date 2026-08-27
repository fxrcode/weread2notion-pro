import logging
import os

from weread2notionpro.logging_config import configure_logging
from weread2notionpro.note_sync import (
    remote_collection_complete,
    select_notebooks_to_sync,
)
from weread2notionpro.notion_helper import NotionHelper
from weread2notionpro.utils import (
    get_block,
    get_heading,
    get_number,
    get_number_from_result,
    get_quote,
    get_rich_text_from_result,
    get_table_of_contents,
)
from weread2notionpro.weread_api import (
    WeReadApi,
    WeReadApiError,
    WeReadApiRateLimitError,
)

# 获取logger实例
logger = logging.getLogger(__name__)
configure_logging()


def get_bookmark_list(page_id, bookId, expected_count=0):
    """获取我的划线"""
    def is_exported_highlight(result):
        type_property = (result.get("properties") or {}).get("type")
        return type_property is None or type_property.get("number") != 0

    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(
        notion_helper.bookmark_database_id, filter
    )
    dict1 = {
        get_rich_text_from_result(x, "bookmarkId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
        # 官方 API 只导出划线，不导出 type=0 的书签；保留历史书签数据。
        if is_exported_highlight(x)
    }
    dict2 = {
        get_rich_text_from_result(x, "blockId"): x.get("id")
        for x in results
        if is_exported_highlight(x)
    }
    bookmarks = weread_api.get_bookmark_list(bookId)
    if not remote_collection_complete(expected_count, len(bookmarks)):
        raise WeReadApiError(
            f"书籍 {bookId} 应有 {expected_count} 条划线，"
            f"API仅返回 {len(bookmarks)} 条；保留现有Notion数据"
        )
    for i in bookmarks:
        if i.get("bookmarkId") in dict1:
            i["blockId"] = dict1.pop(i.get("bookmarkId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return bookmarks


def get_review_list(page_id, bookId, expected_count=0):
    """获取笔记"""
    filter = {
        "and": [
            {"property": "书籍", "relation": {"contains": page_id}},
            {"property": "blockId", "rich_text": {"is_not_empty": True}},
        ]
    }
    results = notion_helper.query_all_by_book(notion_helper.review_database_id, filter)
    dict1 = {
        get_rich_text_from_result(x, "reviewId"): get_rich_text_from_result(
            x, "blockId"
        )
        for x in results
    }
    dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
    reviews = weread_api.get_review_list(bookId)
    if not remote_collection_complete(expected_count, len(reviews)):
        raise WeReadApiError(
            f"书籍 {bookId} 应有 {expected_count} 条想法，"
            f"API仅返回 {len(reviews)} 条；保留现有Notion数据"
        )
    for i in reviews:
        if i.get("reviewId") in dict1:
            i["blockId"] = dict1.pop(i.get("reviewId"))
    for blockId in dict1.values():
        notion_helper.delete_block(blockId)
        notion_helper.delete_block(dict2.get(blockId))
    return reviews


def check(bookId):
    """检查是否已经插入过"""
    filter = {"property": "BookId", "rich_text": {"equals": bookId}}
    response = notion_helper.query(
        database_id=notion_helper.book_database_id, filter=filter
    )
    if len(response["results"]) > 0:
        return response["results"][0]["id"]
    return None


def get_sort():
    """获取database中的最新时间"""
    filter = {"property": "Sort", "number": {"is_not_empty": True}}
    sorts = [
        {
            "property": "Sort",
            "direction": "descending",
        }
    ]
    response = notion_helper.query(
        database_id=notion_helper.book_database_id,
        filter=filter,
        sorts=sorts,
        page_size=1,
    )
    if len(response.get("results")) == 1:
        return response.get("results")[0].get("properties").get("Sort").get("number")
    return 0


def sort_notes(page_id, chapter, bookmark_list):
    """对笔记进行排序"""
    bookmark_list = sorted(
        bookmark_list,
        key=lambda x: (
            x.get("chapterUid", 1),
            0
            if (x.get("range", "") == "" or x.get("range").split("-")[0] == "")
            else int(x.get("range").split("-")[0]),
        ),
    )

    notes = []
    if chapter != None:
        filter = {"property": "书籍", "relation": {"contains": page_id}}
        results = notion_helper.query_all_by_book(
            notion_helper.chapter_database_id, filter
        )
        dict1 = {
            get_number_from_result(x, "chapterUid"): get_rich_text_from_result(
                x, "blockId"
            )
            for x in results
        }
        dict2 = {get_rich_text_from_result(x, "blockId"): x.get("id") for x in results}
        d = {}
        for data in bookmark_list:
            chapterUid = data.get("chapterUid", 1)
            if chapterUid not in d:
                d[chapterUid] = []
            d[chapterUid].append(data)
        for key, value in d.items():
            if key in chapter:
                if key in dict1:
                    chapter.get(key)["blockId"] = dict1.pop(key)
                notes.append(chapter.get(key))
            notes.extend(value)
        for blockId in dict1.values():
            notion_helper.delete_block(blockId)
            notion_helper.delete_block(dict2.get(blockId))
    else:
        notes.extend(bookmark_list)
    return notes


def append_blocks(id, contents):
    logger.info(f"笔记数{len(contents)}")
    before_block_id = ""
    block_children = notion_helper.get_block_children(id)
    if len(block_children) > 0 and block_children[0].get("type") == "table_of_contents":
        before_block_id = block_children[0].get("id")
    else:
        response = notion_helper.append_blocks(
            block_id=id, children=[get_table_of_contents()]
        )
        before_block_id = response.get("results")[0].get("id")
    blocks = []
    sub_contents = []
    l = []
    for content in contents:
        if len(blocks) == 100:
            results = append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
            before_block_id = results[-1].get("blockId")
            l.extend(results)
            blocks.clear()
            sub_contents.clear()
            if not notion_helper.sync_bookmark and content.get("type") == 0:
                continue
            blocks.append(content_to_block(content))
            sub_contents.append(content)
        elif "blockId" in content:
            if len(blocks) > 0:
                l.extend(
                    append_blocks_to_notion(id, blocks, before_block_id, sub_contents)
                )
                blocks.clear()
                sub_contents.clear()
            before_block_id = content["blockId"]
        else:
            if not notion_helper.sync_bookmark and content.get("type") == 0:
                continue
            blocks.append(content_to_block(content))
            sub_contents.append(content)

    if len(blocks) > 0:
        l.extend(append_blocks_to_notion(id, blocks, before_block_id, sub_contents))
    for index, value in enumerate(l):
        logger.info(f"正在插入第{index + 1}条笔记，共{len(l)}条")
        if "bookmarkId" in value:
            notion_helper.insert_bookmark(id, value)
        elif "reviewId" in value:
            notion_helper.insert_review(id, value)
        else:
            notion_helper.insert_chapter(id, value)


def content_to_block(content):
    if "bookmarkId" in content:
        return get_block(
            content.get("markText", ""),
            notion_helper.block_type,
            notion_helper.show_color,
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    elif "reviewId" in content:
        return get_block(
            content.get("content", ""),
            notion_helper.block_type,
            notion_helper.show_color,
            content.get("style"),
            content.get("colorStyle"),
            content.get("reviewId"),
        )
    else:
        return get_heading(content.get("level"), content.get("title"))


def append_blocks_to_notion(id, blocks, after, contents):
    response = notion_helper.append_blocks_after(
        block_id=id, children=blocks, after=after
    )
    results = response.get("results")
    l = []
    for index, content in enumerate(contents):
        result = results[index]
        if content.get("abstract") != None and content.get("abstract") != "":
            notion_helper.append_blocks(
                block_id=result.get("id"), children=[get_quote(content.get("abstract"))]
            )
        content["blockId"] = result.get("id")
        l.append(content)
    return l


weread_api = WeReadApi()
notion_helper = NotionHelper()


def main(target_book_id=None):
    notion_books = notion_helper.get_all_book()
    books = weread_api.get_notebooklist()
    if books is None:
        return

    if target_book_id:
        books = [book for book in books if book.get("bookId") == target_book_id]
        if not books:
            logger.warning(f"未找到指定的书籍 {target_book_id}")
            return
        if target_book_id not in notion_books:
            logger.warning(f"书籍 {target_book_id} 不在Notion数据库中，跳过同步")
            return
        logger.info(f"强制同步指定书籍: {target_book_id}")
    else:
        try:
            sync_limit = max(int(os.getenv("NOTES_SYNC_LIMIT", "100")), 0)
        except ValueError:
            sync_limit = 100
            logger.warning("笔记同步批次上限配置无效，使用默认值 100")
        books, deferred_books, unchanged_books, missing_books = (
            select_notebooks_to_sync(books, notion_books, sync_limit)
        )
        logger.info(
            f"笔记同步计划：本次 {len(books)} 本，延后 {len(deferred_books)} 本，"
            f"无需同步 {len(unchanged_books)} 本，Notion缺失 {len(missing_books)} 本"
        )
        if missing_books:
            missing_preview = ", ".join(
                f"《{book.get('title', '未知书名')}》({book.get('bookId')})"
                for book in missing_books[:10]
            )
            logger.warning(f"Notion缺失书籍示例: {missing_preview}")

    synced_count = 0
    failed_count = 0
    stopped_by_quota = False
    for index, book in enumerate(books):
        try:
            bookId = book.get("bookId")
            title = book.get("title")
            author = book.get("author", "未知作者")
            sort = book.get("sort")
            note_count = book.get("noteCount", 0)
            review_count = book.get("reviewCount", 0)
            bookmark_count = book.get("bookmarkCount", 0)

            logger.info(f"\n{'=' * 60}")
            logger.info(f"开始处理第 {index + 1}/{len(books)} 本书籍")
            logger.info(f"书籍信息: 《{title}》 - {author}")
            logger.info(f"书籍ID: {bookId}")
            logger.info(
                f"笔记统计: {note_count} 个划线, {review_count} 个想法, "
                f"{bookmark_count} 个书签"
            )
            logger.info(f"排序值: {sort}")

            if not title:
                logger.warning(f"跳过书籍 {bookId}，缺少书籍标题")
                continue

            pageId = notion_books.get(bookId).get("pageId")
            logger.info(f"开始同步《{title}》的笔记内容...")

            chapter = weread_api.get_chapter_info(bookId)
            logger.info(f"获取章节信息完成，章节数: {len(chapter) if chapter else 0}")

            bookmark_list = get_bookmark_list(pageId, bookId, note_count)
            logger.info(f"获取标注列表完成，标注数: {len(bookmark_list)}")

            reviews = get_review_list(pageId, bookId, review_count)
            logger.info(f"获取想法列表完成，想法数: {len(reviews)}")

            bookmark_list.extend(reviews)
            total_notes = len(bookmark_list)
            logger.info(f"合并后总笔记数: {total_notes}")

            content = sort_notes(pageId, chapter, bookmark_list)
            logger.info("笔记排序完成，准备写入Notion...")

            append_blocks(pageId, content)
            logger.info("笔记内容写入完成")

            properties = {
                "Sort": get_number(sort),
                "微信读书划线数": get_number(note_count),
                "微信读书想法数": get_number(review_count),
                "微信读书书签数": get_number(bookmark_count),
            }
            notion_helper.update_book_page(page_id=pageId, properties=properties)
            logger.info("书籍属性更新完成")
            logger.info(f"《{title}》同步完成！")
            logger.info(f"{'=' * 60}\n")
            synced_count += 1
        except WeReadApiRateLimitError as exc:
            logger.error(f"微信读书 API 配额不足，停止笔记同步: {exc}")
            stopped_by_quota = True
            break
        except WeReadApiError as exc:
            logger.error(f"书籍 {book.get('bookId')} API数据不完整，跳过: {exc}")
            failed_count += 1
            continue
        except Exception as exc:
            logger.error(f"书籍 {book.get('bookId')} 同步失败，跳过: {exc}")
            failed_count += 1
            continue

    summary = (
        f"笔记同步结束：成功 {synced_count} 本，失败 {failed_count} 本，"
        f"本次未处理 {len(books) - synced_count - failed_count} 本"
    )
    if books and synced_count == 0:
        logger.error(summary)
    elif stopped_by_quota:
        logger.warning(summary)
    else:
        logger.info(summary)


if __name__ == "__main__":
    import sys

    # 支持命令行参数指定书籍ID
    target_book_id = sys.argv[1] if len(sys.argv) > 1 else None
    if target_book_id:
        logger.info(f"命令行指定同步书籍ID: {target_book_id}")
    main(target_book_id)
