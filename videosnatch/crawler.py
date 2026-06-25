"""VideoSnatch Crawler - 基于 CloakBrowser + Playwright 的智能爬虫引擎

完全独立于现有的视频下载功能，不依赖 PyQt5。
可单独使用，也可通过 CLI 调用。

用法:
    from videosnatch.crawler import Crawler

    async with Crawler() as crawler:
        await crawler.navigate("https://example.com")
        text = await crawler.extract_text()
        links = await crawler.extract_links()
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class CrawlerError(Exception):
    """爬虫异常基类"""
    pass


class NavigationError(CrawlerError):
    """导航异常"""
    pass


class ExtractionError(CrawlerError):
    """数据提取异常"""
    pass


# ── 预置反检测脚本（从 interceptor.py 的 DETECTOR_SCRIPT 精简而来）──

STEALTH_SCRIPT = """
() => {
    // 覆盖 webdriver 属性
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    // 覆盖 chrome runtime
    window.chrome = { runtime: {} };
    // 覆盖权限查询
    const origQuery = navigator.permissions.query;
    navigator.permissions.query = (p) => origQuery.call(navigator.permissions, p)
        .then(r => { r.onchange = null; return r; });
    // 隐藏 headless 特征
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
}
"""


class Crawler:
    """基于 CloakBrowser + Playwright 的智能爬虫引擎

    核心功能：
    - 页面导航与内容提取
    - 结构化数据抓取（CSS 选择器规则）
    - 截图与 JS 执行
    - 页面交互（点击、填表、滚动）
    - 会话保持（复用浏览器 profile，和视频下载浏览器共享登录态）
    - 反检测 stealth 模式

    资源管理：
    - 支持 async with 上下文管理器
    - 支持 headless（默认）/ headed 两种模式
    - 复用 browser_profile 目录，Cookie 和会话与主浏览器互通
    """

    def __init__(self, user_data_dir: Optional[str] = None):
        self._user_data_dir = str(user_data_dir) if user_data_dir else str(
            Path(__file__).resolve().parent.parent / "browser_profile"
        )
        self._context = None
        self._page = None
        self._running = False

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    # ═══════════════ 生命周期 ═══════════════════

    async def start(self, headless: bool = True,
                    viewport: Optional[dict] = None,
                    stealth: bool = True,
                    download_dir: Optional[str] = None,
                    **kwargs):
        """启动浏览器

        Args:
            headless: 是否无头模式（默认 True）
            viewport: 视口尺寸，如 {"width": 1920, "height": 1080}
            stealth: 是否启用反检测（默认 True）
            download_dir: 下载目录
            **kwargs: 透传给 launch_persistent_context_async

        Returns: self（支持链式调用）
        """
        from cloakbrowser import launch_persistent_context_async

        opts = dict(kwargs)
        opts.setdefault("headless", headless)
        if stealth:
            opts.setdefault("stealth_args", True)
        if viewport:
            opts["viewport"] = viewport
        else:
            # 默认使用合理视口，避免被检测为 headless
            opts.setdefault("viewport", {"width": 1920, "height": 1080})
        if download_dir:
            # 同步设置 Chrome 下载目录偏好
            self._set_download_dir(download_dir)

        self._context = await launch_persistent_context_async(
            self._user_data_dir, **opts
        )
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()

        # 注入反检测脚本
        if stealth:
            try:
                await self._context.add_init_script(STEALTH_SCRIPT)
                await self._page.evaluate(STEALTH_SCRIPT)
            except Exception as e:
                logger.debug(f"反检测脚本注入失败: {e}")

        self._running = True
        logger.info(
            f"Crawler 启动成功 "
            f"(headless={headless}, profile={self._user_data_dir})"
        )
        return self

    async def close(self):
        """关闭浏览器，清理资源"""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug(f"关闭浏览器异常: {e}")
        self._context = None
        self._page = None
        self._running = False
        logger.info("Crawler 已关闭")

    def is_running(self) -> bool:
        return self._running

    def _set_download_dir(self, path: str):
        """设置 Chrome 下载目录（启动前调用）"""
        try:
            prefs_file = (
                Path(self._user_data_dir) / "Default" / "Preferences"
            )
            if prefs_file.exists():
                data = json.loads(prefs_file.read_text("utf-8"))
            else:
                data = {}
            if "savefile" not in data:
                data["savefile"] = {}
            data["savefile"]["default_directory"] = path
            if "download" not in data:
                data["download"] = {}
            data["download"]["default_directory"] = path
            prefs_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
            )
        except Exception as e:
            logger.debug(f"设置下载目录失败: {e}")

    # ═══════════════ 导航 ═══════════════════════

    async def navigate(self, url: str,
                       wait_until: str = "networkidle",
                       timeout: int = 30000) -> str:
        """导航到指定 URL

        Args:
            url: 目标 URL（自动补 https://）
            wait_until: 等待条件
                - "load": load 事件触发
                - "domcontentloaded": DOM 解析完成
                - "networkidle": 网络空闲（默认）
            timeout: 超时时间（毫秒）

        Returns: 最终 URL（可能经过重定向）

        Raises: NavigationError
        """
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            resp = await self._page.goto(
                url, wait_until=wait_until, timeout=timeout
            )
            if resp is None:
                raise NavigationError(f"导航到 {url} 无响应")
            if resp.status >= 400:
                logger.warning(f"页面返回 HTTP {resp.status}: {url}")
            return self._page.url
        except Exception as e:
            raise NavigationError(f"导航失败: {e}") from e

    async def wait_for_stable(self, timeout_ms: int = 15000):
        """等待页面网络空闲"""
        try:
            await self._page.wait_for_load_state(
                "networkidle", timeout=timeout_ms
            )
        except Exception:
            pass

    async def go_back(self):
        """后退"""
        await self._page.go_back()

    async def refresh(self):
        """刷新页面"""
        await self._page.reload()

    async def get_current_url(self) -> str:
        """获取当前页面 URL"""
        try:
            return self._page.url
        except Exception:
            return ""

    # ═══════════════ 内容提取 ═══════════════════

    async def extract_text(self, selector: Optional[str] = None,
                           max_length: int = 0) -> str:
        """提取页面可见文本

        Args:
            selector: CSS 选择器（None=整个页面）
            max_length: 最大字符数（0=不限）

        Returns: 纯文本内容
        """
        if selector:
            script = f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    return el ? el.textContent.trim() : '';
                }})()
            """
        else:
            script = "document.body.innerText"
        text = await self._page.evaluate(script)
        if max_length > 0 and len(text) > max_length:
            text = text[:max_length] + "..."
        return text.strip()

    async def extract_html(self, selector: Optional[str] = None,
                           outer: bool = True) -> str:
        """提取页面 HTML

        Args:
            selector: CSS 选择器
            outer: True=outerHTML, False=innerHTML

        Returns: HTML 字符串
        """
        prop = "outerHTML" if outer else "innerHTML"
        if selector:
            script = f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selector)});
                    return el ? el.{prop} : '';
                }})()
            """
        else:
            script = f"document.documentElement.{prop}"
        return await self._page.evaluate(script)

    async def extract_links(self) -> list[dict]:
        """提取页面所有链接

        Returns:
            [{"text": str, "href": str, "title": str}, ...]
        """
        return await self._page.evaluate("""
            Array.from(document.querySelectorAll('a[href]'))
                .map(a => ({
                    text: (a.textContent || '').trim().slice(0, 200),
                    href: a.href,
                    title: (a.title || '').trim(),
                }))
                .filter(l => l.href.startsWith('http'))
        """)

    async def extract_images(self) -> list[dict]:
        """提取页面所有图片

        Returns:
            [{"src": str, "alt": str, "width": int, "height": int}, ...]
        """
        return await self._page.evaluate("""
            Array.from(document.querySelectorAll('img[src]'))
                .map(img => ({
                    src: img.currentSrc || img.src,
                    alt: (img.alt || '').trim(),
                    width: img.naturalWidth || img.width,
                    height: img.naturalHeight || img.height,
                }))
                .filter(i => i.src.startsWith('http'))
        """)

    async def extract_structured(self, rules: dict[str, str]) -> dict:
        """按 CSS 选择器规则批量提取数据

        选择器语法:
            - "h1"            → 元素的 textContent
            - "img::attr(src)" → 元素的属性值
            - "div::html"     → 元素的 innerHTML
            - "meta::attr(content)" → meta 标签 content 属性

        Args:
            rules: {字段名: CSS选择器}

        Returns:
            {字段名: 值}

        示例:
            rules = {
                "title": "h1",
                "description": "meta[name=description]::attr(content)",
                "price": ".price",
                "image": "img.main-img::attr(src)",
            }
        """
        script = f"""
        (() => {{
            const rules = {json.dumps(rules)};
            const result = {{}};
            for (const [key, selector] of Object.entries(rules)) {{
                const parts = selector.split('::');
                const css = parts[0];
                const modifier = parts[1] || 'text';
                const el = document.querySelector(css);
                if (!el) {{ result[key] = null; continue; }}
                if (modifier.startsWith('attr(')) {{
                    const attrName = modifier.slice(5, -1);
                    result[key] = el.getAttribute(attrName);
                }} else if (modifier === 'html') {{
                    result[key] = el.innerHTML.trim();
                }} else if (modifier === 'text') {{
                    result[key] = el.textContent.trim();
                }} else {{
                    result[key] = el.textContent.trim();
                }}
            }}
            return result;
        }})()
        """
        return await self._page.evaluate(script)

    async def extract_table(self, selector: str) -> list[list[str]]:
        """提取 HTML 表格为二维数组

        Args:
            selector: 表格的 CSS 选择器

        Returns:
            [[col1, col2, ...], ...]  每个 <tr> 一行
        """
        return await self._page.evaluate(f"""
            (() => {{
                const table = document.querySelector({json.dumps(selector)});
                if (!table) return [];
                return Array.from(table.querySelectorAll('tr')).map(row =>
                    Array.from(row.querySelectorAll('td, th')).map(cell =>
                        cell.textContent.trim()
                    )
                );
            }})()
        """)

    async def extract_all(self, selector: str) -> list[dict]:
        """提取所有匹配 CSS 选择器的元素信息

        Args:
            selector: CSS 选择器

        Returns:
            [{"text": str, "html": str, "attrs": {}}, ...]
        """
        return await self._page.evaluate(f"""
            (() => {{
                return Array.from(
                    document.querySelectorAll({json.dumps(selector)})
                ).map(el => ({{
                    text: (el.textContent || '').trim().slice(0, 500),
                    html: el.innerHTML.trim().slice(0, 1000),
                    tag: el.tagName.toLowerCase(),
                    attrs: Object.fromEntries(
                        Array.from(el.attributes).map(a => [a.name, a.value])
                    ),
                }}));
            }})()
        """)

    async def extract_metadata(self) -> dict:
        """提取页面元数据

        Returns:
            {title, url, description, keywords, og_title, og_image, lang, charset}
        """
        return await self._page.evaluate("""
            (() => {
                const get = (sel, attr) => {
                    const el = document.querySelector(sel);
                    return el ? (attr ? (el.getAttribute(attr) || '') : (el.textContent || '').trim()) : '';
                };
                return {
                    title: document.title,
                    url: location.href,
                    description: get('meta[name=description]', 'content'),
                    keywords: get('meta[name=keywords]', 'content'),
                    og_title: get('meta[property="og:title"]', 'content'),
                    og_description: get('meta[property="og:description"]', 'content'),
                    og_image: get('meta[property="og:image"]', 'content'),
                    charset: document.characterSet,
                    lang: document.documentElement.lang || '',
                };
            })()
        """)

    # ═══════════════ 截图 ═══════════════════════

    async def screenshot(self, path: str,
                         full_page: bool = True) -> str:
        """页面截图

        Args:
            path: 保存路径
            full_page: 是否全页截图

        Returns: 截图文件路径
        """
        await self._page.screenshot(path=path, full_page=full_page)
        logger.info(f"截图已保存: {path}")
        return path

    # ═══════════════ JS 执行 ═══════════════════

    async def execute_js(self, script: str) -> Any:
        """在页面中执行 JavaScript

        Args:
            script: JS 代码

        Returns: JS 返回值
        """
        return await self._page.evaluate(script)

    # ═══════════════ 页面交互 ═══════════════════

    async def wait_for_element(self, selector: str,
                               timeout: int = 10000,
                               state: str = "visible"):
        """等待元素出现

        Args:
            selector: CSS 选择器
            timeout: 超时（毫秒）
            state: 等待状态 - "visible", "hidden", "attached", "detached"
        """
        await self._page.wait_for_selector(
            selector, timeout=timeout, state=state
        )

    async def click(self, selector: str):
        """点击元素"""
        await self._page.click(selector)

    async def fill(self, selector: str, value: str):
        """填写输入框"""
        await self._page.fill(selector, value)

    async def select_option(self, selector: str, value: str):
        """选择下拉选项"""
        await self._page.select_option(selector, value)

    async def get_text(self, selector: str) -> str:
        """获取元素文本"""
        return await self._page.inner_text(selector)

    async def get_attribute(self, selector: str, attr: str) -> Optional[str]:
        """获取元素属性"""
        return await self._page.get_attribute(selector, attr)

    async def scroll_to_bottom(self, step: int = 800,
                               delay: float = 1.0,
                               max_steps: int = 50) -> int:
        """滚动到底部（处理无限滚动加载）

        Args:
            step: 每次滚动像素
            delay: 每次滚动后等待秒数
            max_steps: 最大步数

        Returns: 实际滚动步数
        """
        for i in range(max_steps):
            prev = await self._page.evaluate("document.body.scrollHeight")
            await self._page.evaluate(f"window.scrollBy(0, {step})")
            await asyncio.sleep(delay)
            cur = await self._page.evaluate("document.body.scrollHeight")
            if cur == prev:
                return i + 1
        return max_steps

    # ═══════════════ Cookie 管理 ═══════════════

    async def get_cookies(self) -> list[dict]:
        """获取当前页面的 Cookie"""
        return await self._context.cookies()

    async def set_cookies(self, cookies: list[dict]):
        """设置 Cookie"""
        await self._context.add_cookies(cookies)

    async def clear_cookies(self):
        """清除所有 Cookie"""
        await self._context.clear_cookies()

    # ═══════════════ 智能提取 ═══════════════════

    async def extract(self, mode: str = "auto") -> dict:
        """智能提取：自动判断页面类型并提取适当数据

        Args:
            mode:
                - "auto":   自动判断（默认）
                - "text":   纯文本
                - "full":   文本 + 链接 + 图片 + 元数据
                - "links":  仅链接
                - "metadata": 仅元数据

        Returns:
            {"url": str, "metadata": {...}, "text"?: str, "links"?: [...], "images"?: [...]}
        """
        result = {
            "url": await self.get_current_url(),
            "metadata": await self.extract_metadata(),
        }

        if mode in ("auto", "text"):
            result["text"] = await self.extract_text()

        if mode == "full":
            result["text"] = await self.extract_text()
            result["links"] = await self.extract_links()
            result["images"] = await self.extract_images()

        if mode == "links":
            result["links"] = await self.extract_links()

        return result


# ═══════════════ 便捷函数 ═══════════════════════

async def quick_crawl(url: str, mode: str = "auto",
                      headless: bool = True,
                      profile: Optional[str] = None,
                      timeout: int = 30000) -> dict:
    """一键爬取：快速获取页面数据

    Args:
        url: 目标 URL
        mode: 提取模式（同 Crawler.extract）
        headless: 是否无头
        profile: 浏览器 profile 目录
        timeout: 导航超时

    Returns: 提取的数据字典
    """
    async with Crawler(user_data_dir=profile) as c:
        await c.navigate(url, timeout=timeout)
        return await c.extract(mode=mode)
