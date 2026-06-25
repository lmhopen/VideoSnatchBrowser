"""bookmark_importer.py - 导入 Netscape HTML 书签文件到 Chrome/CloakBrowser 配置文件

支持解析标准 HTML 书签导出文件（Netscape 格式，所有主流浏览器通用），
转换为 Chrome 浏览器使用的 JSON 格式 Bookmarks 文件。
"""

import json
import shutil
import time
import uuid
import logging
import html
from pathlib import Path
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# ── Chrome 书签 JSON 格式工具 ──────────────────────────

# Chrome 时间：1601-01-01 UTC 以来的微秒数
# Unix 时间戳转 Chrome 时间
WINDOWS_EPOCH_US = 11644473600000000  # microseconds from 1601-01-01 to 1970-01-01


def _chrome_time(timestamp: float = 0) -> str:
    """将 Unix 时间戳转为 Chrome 书签 JSON 使用的字符串时间。
    如果未提供时间戳，使用当前时间。
    """
    if timestamp <= 0:
        timestamp = time.time()
    us = int(timestamp * 1_000_000) + WINDOWS_EPOCH_US
    return str(us)


def _make_guid() -> str:
    """生成 Chrome 风格的 GUID（小写，带连字符）"""
    return str(uuid.uuid4()).lower()


def _make_url_node(url: str, title: str, add_date: float = 0) -> dict:
    """创建一个 Chrome 书签 URL 节点"""
    return {
        "date_added": _chrome_time(add_date),
        "guid": _make_guid(),
        "id": 0,  # placeholder, filled later
        "name": title,
        "type": "url",
        "url": url,
    }


def _make_folder_node(name: str, children: list, add_date: float = 0) -> dict:
    """创建一个 Chrome 书签文件夹节点"""
    return {
        "children": children,
        "date_added": _chrome_time(add_date),
        "date_modified": _chrome_time(),
        "guid": _make_guid(),
        "id": 0,  # placeholder, filled later
        "name": name,
        "type": "folder",
    }


def _assign_ids(nodes: list, next_id: list) -> None:
    """递归为节点分配自增 ID"""
    for node in nodes:
        node["id"] = next_id[0]
        next_id[0] += 1
        if node.get("type") == "folder" and "children" in node:
            _assign_ids(node["children"], next_id)


def _make_bookmarks_json(bookmark_bar_children: list,
                          other_children: list) -> dict:
    """构建完整的 Chrome Bookmarks JSON 结构"""
    roots = {
        "bookmark_bar": _make_folder_node("书签栏", bookmark_bar_children),
        "other": _make_folder_node("其他书签", other_children),
        "synced": _make_folder_node("移动设备书签", []),
    }
    # 为根文件夹分配固定 ID
    roots["bookmark_bar"]["id"] = 1
    roots["other"]["id"] = 2
    roots["synced"]["id"] = 3

    # 为所有子节点分配自增 ID（从 4 开始）
    next_id = [4]
    _assign_ids(roots["bookmark_bar"].get("children", []), next_id)
    _assign_ids(roots["other"].get("children", []), next_id)
    _assign_ids(roots["synced"].get("children", []), next_id)

    return {
        "checksum": "0000000000000000000000000000000000000000",
        "roots": roots,
        "version": 1,
    }


# ── HTML 书签文件解析 ──────────────────────────────

class BookmarkParseError(Exception):
    """书签文件解析错误"""
    pass


class NetscapeBookmarkParser(HTMLParser):
    """解析 Netscape 格式的 HTML 书签文件

    标准 Netscape 书签格式结构：
        <H1>页面标题</H1>          ← 页面标题，不是真实文件夹
        <DL>                       ← 根目录
            <DT><H3>文件夹名</H3>  ← 文件夹标题
            <DL>                   ← 文件夹内容
                <DT><A HREF="...">书签标题</A>
            </DL>
            <DT><A HREF="...">书签标题</A>  ← 根目录下的书签
        </DL>
    """

    # 只有这些标签内的文本才算文件夹名（H1 是页面标题，排除）
    FOLDER_HEADING_TAGS = {"h2", "h3", "h4", "h5", "h6", "dt"}

    def __init__(self):
        super().__init__()
        self._stack: list[list] = []  # 当前路径栈（仅非根 DL 的子节点列表）
        self._current_text = ""        # 正在积累的文本内容
        self._current_attrs: dict = {}  # 当前标签的属性
        self._current_tag = ""         # 正在处理的标签名
        self._text_from_heading = False  # 当前文本是否来自文件夹标题标签
        self._bookmark_bar: list = []  # 书签栏节点
        self._other: list = []         # 其他书签节点
        self._dl_depth = 0             # DL 嵌套深度（根 DL=1, 子 DL=2...）

    def _current_list(self) -> list:
        """返回当前正在添加子节点的列表"""
        if self._stack:
            return self._stack[-1]
        if self._dl_depth == 0:
            # 不在任何 DL 内 → 其他书签
            return self._other
        # 在最外层 DL 内且无子文件夹栈 → 书签栏
        return self._bookmark_bar

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_dict = dict(attrs)
        self._current_tag = tag

        if tag == "dl":
            self._dl_depth += 1

            # 如果当前有来自文件夹标题标签的文本内容，则创建文件夹
            if (self._current_text.strip()
                    and self._text_from_heading
                    and self._current_attrs.get("href") is None):
                folder_name = self._current_text.strip()
                add_date_str = self._current_attrs.get("add_date", "0")
                try:
                    add_date = float(add_date_str)
                except (ValueError, TypeError):
                    add_date = 0
                new_children = []
                folder = _make_folder_node(folder_name, new_children, add_date)
                self._current_list().append(folder)
                self._stack.append(new_children)
            elif self._stack:
                # 非根目录的无标题 DL，推入空占位列表
                self._stack.append([])
            # else: 根目录 DL（stack 为空），不推入占位，
            #       _current_list() 将直接返回 _bookmark_bar

            # 重置文本和属性状态
            self._current_text = ""
            self._current_attrs = {}
            self._text_from_heading = False

        elif tag in self.FOLDER_HEADING_TAGS:
            # 文件夹标题标签
            self._text_from_heading = True
            self._current_text = ""
            self._current_attrs = {
                "add_date": attrs_dict.get("add_date", "0"),
            }
        elif tag == "a":
            href = attrs_dict.get("href", "")
            add_date_str = attrs_dict.get("add_date", "0")
            try:
                add_date = float(add_date_str)
            except (ValueError, TypeError):
                add_date = 0
            self._current_attrs = {"href": href, "add_date": add_date}
            self._current_text = ""
            self._text_from_heading = False

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "dl":
            self._dl_depth -= 1
            if self._stack:
                self._stack.pop()
        elif tag == "a":
            title = html.unescape(self._current_text.strip()) or "无标题"
            href = self._current_attrs.get("href", "")
            add_date = self._current_attrs.get("add_date", 0)
            if href and title:
                node = _make_url_node(href, title, add_date)
                self._current_list().append(node)
            self._current_text = ""
            self._current_attrs = {}
            self._text_from_heading = False
        elif tag in self.FOLDER_HEADING_TAGS:
            # 文件夹标题结束，保留文本供后续 <DL> 使用
            pass

    def handle_data(self, data):
        self._current_text += data

    def handle_entityref(self, name):
        self._current_text += f"&{name};"

    def handle_charref(self, name):
        self._current_text += f"&#{name};"

    def handle_comment(self, data):
        # 忽略注释内容
        pass


def parse_bookmark_html(filepath: str) -> dict:
    """解析 Netscape HTML 书签文件，返回 Chrome JSON 格式的书签数据

    Args:
        filepath: HTML 书签文件路径

    Returns:
        Chrome 书签 JSON 数据字典

    Raises:
        BookmarkParseError: 文件不存在或解析失败
        ValueError: 文件格式不正确
    """
    path = Path(filepath)
    if not path.exists():
        raise BookmarkParseError(f"书签文件不存在: {filepath}")
    if not path.is_file():
        raise BookmarkParseError(f"路径不是文件: {filepath}")

    try:
        content = path.read_text("utf-8-sig")  # 自动处理 BOM
    except UnicodeDecodeError:
        try:
            content = path.read_text("gbk")  # 尝试 GBK 编码（中文系统常见）
        except UnicodeDecodeError:
            content = path.read_text("utf-8", errors="replace")

    if "DOCTYPE" not in content.upper() and "<DL>" not in content.upper():
        raise ValueError("文件不是 Netscape 书签格式（缺少 DOCTYPE 或 <DL> 标签）")

    parser = NetscapeBookmarkParser()
    try:
        parser.feed(content)
    except Exception as e:
        raise BookmarkParseError(f"解析书签 HTML 失败: {e}")

    bookmark_bar = parser._bookmark_bar
    other = parser._other

    # 如果书签栏为空但有其他书签，尝试合并
    if not bookmark_bar and other:
        bookmark_bar = other
        other = []

    # Chrome 根文件夹名称——导入时应解包（把子项提升到父级）
    CHROME_ROOT_NAMES = {
        "", "书签栏", "书签菜单", "其他书签", "移动设备书签",
        "Bookmarks Bar", "Bookmarks Menu", "Other Bookmarks", "Mobile Bookmarks",
    }

    def _unwrap_chrome_roots(nodes: list) -> list:
        """递归解包 Chrome 根目录文件夹，将其子项提升到父级位置。

        例如 Chrome 导出的 HTML 中第一层是 <H3>书签栏</H3>，
        里面才是真正的书签，解包后这些书签直接成为书签栏的子项。
        """
        result = []
        for node in nodes:
            if node.get("type") == "folder":
                children = _unwrap_chrome_roots(node.get("children", []))
                node["children"] = children
                if node.get("name") in CHROME_ROOT_NAMES:
                    # 解包：文件夹本身不要，把子项提升上来
                    result.extend(children)
                elif children:
                    # 有内容的普通文件夹，保留
                    result.append(node)
                # else: 空文件夹，丢弃
            else:
                result.append(node)
        return result

    bookmark_bar = _unwrap_chrome_roots(bookmark_bar)
    other = _unwrap_chrome_roots(other)

    return _make_bookmarks_json(bookmark_bar, other)


# ── 写入 Chrome 配置文件 ──────────────────────────

def write_to_profile(bookmarks_data: dict, profile_dir: str) -> str:
    """将书签 JSON 数据写入 Chrome 配置文件的 Bookmarks 文件

    Args:
        bookmarks_data: Chrome 书签 JSON 数据
        profile_dir: Chrome 配置文件目录路径（通常是 .../browser_profile/Default）

    Returns:
        写入的 Bookmarks 文件路径

    Raises:
        IOError: 写入失败
    """
    profile = Path(profile_dir)
    if not profile.exists():
        profile.mkdir(parents=True, exist_ok=True)

    # 先备份现有的 Bookmarks 文件（如果有）
    bookmarks_path = profile / "Bookmarks"
    if bookmarks_path.exists():
        bak_path = profile / "Bookmarks.bak"
        try:
            shutil.copy2(str(bookmarks_path), str(bak_path))
            logger.info(f"已备份原有书签: {bak_path}")
        except Exception as e:
            logger.warning(f"备份原有书签失败: {e}")

    # 写入新的 Bookmarks 文件（格式化 JSON，Chrome 使用 4 空格缩进）
    json_str = json.dumps(bookmarks_data, ensure_ascii=False, indent=2)
    bookmarks_path.write_text(json_str, "utf-8")
    logger.info(f"书签已写入: {bookmarks_path}")

    # 同时写入 Bookmarks.bak（Chrome 有时会读取这个）
    bak_path = profile / "Bookmarks.bak"
    bak_path.write_text(json_str, "utf-8")

    return str(bookmarks_path)


def import_bookmarks_from_html(html_filepath: str, profile_dir: str) -> dict:
    """从 HTML 书签文件导入到 Chrome 配置文件（一站式接口）

    Args:
        html_filepath: 输入的 HTML 书签文件路径
        profile_dir: Chrome 配置文件目录路径

    Returns:
        包含导入结果的字典：{"count": int, "filepath": str, "message": str}

    Raises:
        BookmarkParseError: 解析失败
        ValueError: 格式错误
        IOError: 写入失败
    """
    data = parse_bookmark_html(html_filepath)
    written = write_to_profile(data, profile_dir)

    # 统计书签数量
    def _count_urls(nodes: list) -> int:
        count = 0
        for node in nodes:
            if node.get("type") == "url":
                count += 1
            elif node.get("type") == "folder" and "children" in node:
                count += _count_urls(node["children"])
        return count

    root_children = data.get("roots", {}).get("bookmark_bar", {}).get("children", [])
    other_children = data.get("roots", {}).get("other", {}).get("children", [])
    total = _count_urls(root_children) + _count_urls(other_children)

    return {
        "count": total,
        "filepath": written,
        "message": f"成功导入 {total} 个书签",
    }
