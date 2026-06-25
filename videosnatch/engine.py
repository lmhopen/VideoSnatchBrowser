"""VideoSnatch Browser Engine - 基于 CloakBrowser"""

import asyncio
import logging
import os
import threading
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal, QThread, QTimer

logger = logging.getLogger(__name__)


class AsyncEngineWorker(QThread):
    """在后台线程中运行 asyncio 事件循环"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.loop = None
        self._ready = threading.Event()

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def wait_loop_ready(self, timeout=10):
        """等待事件循环就绪"""
        return self._ready.wait(timeout=timeout)

    def stop(self):
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)


class BrowserEngine(QObject):
    """管理 CloakBrowser 生命周期的引擎管理器

    在 headed 模式下启动 CloakBrowser，提供 CDP 会话用于网络拦截，
    支持导航控制和页面稳定等待。
    """

    page_changed = pyqtSignal(str)       # 页面 URL 变化
    browser_crashed = pyqtSignal(str)    # 浏览器崩溃
    browser_ready = pyqtSignal()         # 浏览器就绪
    browser_closed = pyqtSignal()        # 浏览器关闭
    navigation_error = pyqtSignal(str)   # 导航错误
    page_console_message = pyqtSignal(str)  # 页面 console 消息（视频检测用）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._browser = None
        self._context = None
        self._page = None
        self._cdp_session = None
        self._current_url = ""
        self._worker = None
        self._crash_count = 0
        self._max_crash_retries = 3
        self._running = False
        self._stable_timer = None
        self._last_nav_time = 0
        self._pending_nav = False

    # ── 浏览器下载目录管理 ─────────────────────────

    @staticmethod
    def get_browser_download_dir() -> str:
        """从 Preferences 中读取浏览器下载目录"""
        try:
            import json
            from pathlib import Path
            prefs_file = Path(__file__).resolve().parent.parent / "browser_profile" / "Default" / "Preferences"
            if prefs_file.exists():
                data = json.loads(prefs_file.read_text("utf-8"))
                dd = data.get("savefile", {}).get("default_directory", "")
                if dd:
                    return str(dd)
        except Exception:
            pass
        return ""

    @staticmethod
    def set_browser_download_dir(path: str):
        """在 Preferences 中设置浏览器下载目录（启动前调用）"""
        try:
            import json
            from pathlib import Path
            prefs_file = Path(__file__).resolve().parent.parent / "browser_profile" / "Default" / "Preferences"
            if prefs_file.exists():
                data = json.loads(prefs_file.read_text("utf-8"))
            else:
                data = {}
            if "savefile" not in data:
                data["savefile"] = {}
            data["savefile"]["default_directory"] = path
            # 同时设置 download.default_directory（兼容旧版）
            if "download" not in data:
                data["download"] = {}
            data["download"]["default_directory"] = path
            prefs_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
            logger.info(f"浏览器下载目录已设为: {path}")
        except Exception as e:
            logger.error(f"设置浏览器下载目录失败: {e}")

    # ── 公共方法 ──────────────────────────────────

    def _get_loop(self):
        """安全获取事件循环，等待就绪"""
        if not self._worker:
            return None
        if not self._worker.wait_loop_ready(timeout=15):
            return None
        return self._worker.loop

    def start(self, **kwargs):
        """异步启动浏览器（非阻塞，通过信号通知结果）"""
        if self._running:
            logger.warning("浏览器已在运行")
            return True

        self._worker = AsyncEngineWorker()
        self._worker.start()
        self._kwargs = kwargs

        loop = self._get_loop()
        if loop is None:
            logger.error("事件循环启动超时")
            self.browser_crashed.emit("事件循环启动超时")
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._async_start(**kwargs), loop
        )

        def _on_start_done(f):
            try:
                result = f.result()
                self._running = result
                if result:
                    logger.info("CloakBrowser 启动成功")
                    self.browser_ready.emit()
                else:
                    self.browser_crashed.emit("启动返回失败")
            except Exception as e:
                logger.error(f"启动浏览器失败: {e}")
                self.browser_crashed.emit(str(e))

        future.add_done_callback(_on_start_done)
        return True

    def navigate(self, url: str):
        """导航到指定 URL"""
        if not self._running or not self._page:
            self.navigation_error.emit("浏览器未就绪")
            return False

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        loop = self._get_loop()
        if loop is None:
            self.navigation_error.emit("事件循环未就绪")
            return False

        future = asyncio.run_coroutine_threadsafe(
            self._async_navigate(url), loop
        )
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error(f"导航失败: {e}")
            self.navigation_error.emit(str(e))
            return False

    def go_back(self):
        if not self._page:
            return
        loop = self._get_loop()
        if not loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._page.go_back(), loop
        )

    def go_forward(self):
        if not self._page:
            return
        loop = self._get_loop()
        if not loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._page.go_forward(), loop
        )

    def refresh(self):
        if not self._page:
            return
        loop = self._get_loop()
        if not loop:
            return
        asyncio.run_coroutine_threadsafe(
            self._page.reload(), loop
        )

    def get_current_url(self) -> str:
        if self._page:
            loop = self._get_loop()
            if not loop:
                return self._current_url
            future = asyncio.run_coroutine_threadsafe(
                self._async_get_url(), loop
            )
            try:
                return future.result(timeout=5)
            except Exception:
                pass
        return self._current_url

    def get_page(self):
        return self._page

    def get_cdp_session(self):
        return self._cdp_session

    def get_browser(self):
        return self._browser

    def execute_js(self, script: str):
        """在页面中执行 JavaScript"""
        if not self._page:
            return None
        loop = self._get_loop()
        if not loop:
            return None
        future = asyncio.run_coroutine_threadsafe(
            self._page.evaluate(script), loop
        )
        try:
            return future.result(timeout=10)
        except Exception as e:
            logger.error(f"JS 执行失败: {e}")
            return None

    def wait_for_stable(self, timeout_ms=15000):
        """等待页面稳定（无网络活动）"""
        if not self._page:
            return True
        loop = self._get_loop()
        if not loop:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._async_wait_for_stable(timeout_ms), loop
        )
        try:
            return future.result(timeout=(timeout_ms // 1000) + 5)
        except Exception:
            return False

    def stop(self):
        """关闭浏览器并清理"""
        if not self._running:
            return
        self._running = False
        loop = self._get_loop()
        if not loop:
            if self._worker:
                self._worker.stop()
                self._worker.wait(3000)
            return
        future = asyncio.run_coroutine_threadsafe(
            self._async_stop(), loop
        )
        try:
            future.result(timeout=15)
        except Exception:
            pass
        if self._worker:
            self._worker.stop()
            self._worker.wait(5000)
            self._worker = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp_session = None

    def is_running(self) -> bool:
        return self._running

    # ── 异步内部方法 ──────────────────────────────

    async def _async_start(self, **kwargs):
        """实际的异步启动逻辑"""
        try:
            from cloakbrowser import launch_persistent_context_async

            from videosnatch.interceptor import DETECTOR_SCRIPT

            # 持久化浏览器用户数据目录（书签、Cookie、历史记录等）
            # 注意：不删除已存在的目录，否则会丢失用户导入的收藏夹
            app_dir = Path(__file__).resolve().parent.parent
            user_data_dir = app_dir / "browser_profile"
            user_data_dir.mkdir(parents=True, exist_ok=True)

            # ★ 在浏览器启动前设置下载目录（对应网页点击下载的文件）
            download_dir = kwargs.pop("download_dir", "")
            if download_dir:
                self.set_browser_download_dir(download_dir)

            # 默认非静默模式 + stealth 防检测 + 禁用固定视口（使用实际窗口大小）
            opts = dict(kwargs)
            opts.setdefault("headless", False)
            opts.setdefault("stealth_args", True)
            opts.setdefault("viewport", None)  # 使用窗口实际尺寸，避免高分辨率屏幕下页面显示不全

            # launch_persistent_context_async 返回 BrowserContext（不是 Browser）
            # 用户数据目录下的所有状态（书签、Cookie、localStorage 等）都会自动持久化
            self._context = await launch_persistent_context_async(
                str(user_data_dir), **opts
            )
            self._browser = self._context.browser

            # ★ 使用 Playwright 原生 API 注入检测脚本到每个新页面（比 CDP 更可靠）
            # 注意：add_init_script 是 async 方法，必须 await！
            await self._context.add_init_script(DETECTOR_SCRIPT)

            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()

            # ★ 也在当前页面上立即执行一次脚本
            try:
                await self._page.evaluate(DETECTOR_SCRIPT)
            except Exception as e:
                logger.debug(f"当前页面注入脚本失败（导航后会自动生效）: {e}")

            self._page.on("close", self._on_page_closed)
            self._page.on("crash", self._on_page_crash)
            self._page.on("framenavigated", self._on_frame_navigated)

            # ★ 用 Playwright 的 console 事件监听（比 CDP Console.enable 更稳定）
            self._page.on("console", self._on_page_console)

            try:
                cdp = await self._context.new_cdp_session(self._page)
                self._cdp_session = cdp
                logger.info("CDP 会话创建成功")
            except Exception as e:
                logger.warning(f"创建 CDP 会话失败（不影响视频检测）: {e}")

            self._crash_count = 0
            logger.info("CloakBrowser 启动成功 (persistent profile)")
            return True

        except ImportError as e:
            logger.error(f"导入 CloakBrowser 失败: {e}")
            raise
        except Exception as e:
            logger.error(f"启动 CloakBrowser 失败: {e}")
            raise

    async def _async_navigate(self, url: str) -> bool:
        try:
            self._pending_nav = True
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self._async_wait_for_stable(8000)
            self._current_url = self._page.url
            self.page_changed.emit(self._current_url)
            self._pending_nav = False
            return response is not None
        except Exception as e:
            self._pending_nav = False
            logger.error(f"导航到 {url} 失败: {e}")
            self.navigation_error.emit(str(e))
            return False

    async def _async_get_url(self) -> str:
        try:
            return self._page.url
        except Exception:
            return self._current_url

    async def _async_wait_for_stable(self, timeout_ms=15000):
        """等待网络空闲"""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def _async_stop(self):
        try:
            if self._cdp_session:
                try:
                    await self._cdp_session.detach()
                except Exception:
                    pass
                self._cdp_session = None
            # 如果使用了 persistent context，关闭 context（也会自动关闭 browser 和 Playwright）
            if self._context:
                await self._context.close()
            elif self._browser:
                await self._browser.close()
        except Exception as e:
            logger.error(f"关闭浏览器失败: {e}")

    # ── 事件处理 ──────────────────────────────────

    def _on_page_closed(self):
        logger.warning("页面被关闭")
        self.browser_closed.emit()

    def _on_page_crash(self, page):
        logger.error("页面崩溃")
        self._crash_count += 1
        if self._crash_count <= self._max_crash_retries:
            self.browser_crashed.emit(f"页面崩溃 (第{self._crash_count}次)")
            self._try_restart()
        else:
            self.browser_crashed.emit("页面崩溃次数过多")

    async def _async_on_frame_navigated(self, frame):
        if frame == self._page.main_frame:
            url = frame.url
            if url and url != "about:blank":
                self._current_url = url
                self.page_changed.emit(url)
                # ★ 每次导航后检查并确保 UI 按钮存在
                # （add_init_script 应在导航时自动注入，此步骤作为安全兜底）
                try:
                    from videosnatch.interceptor import DETECTOR_SCRIPT
                    has_btn = await self._page.evaluate("!!document.getElementById('__vs_btn')")
                    if not has_btn:
                        await self._page.evaluate(DETECTOR_SCRIPT)
                except Exception as inj_err:
                    logger.debug(f"导航后 UI 注入失败: {inj_err}")

    def _on_page_console(self, msg):
        """处理 Playwright 页面 console 消息（转发给 Python 端的检测器）"""
        try:
            text = msg.text
            if text and (text.startswith("__VSNATCH__:") or text.startswith("__VSNATCH_DOWNLOAD__:")):
                self.page_console_message.emit(text)
        except Exception:
            pass

    def _on_frame_navigated(self, frame):
        try:
            loop = self._get_loop()
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._async_on_frame_navigated(frame), loop
                )
        except Exception:
            pass

    def _try_restart(self):
        """尝试重启浏览器"""
        logger.info("尝试重启浏览器...")
        loop = self._get_loop()
        if not loop:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_stop(), loop
            )
            future.result(timeout=10)
        except Exception:
            pass
        self.start(**getattr(self, "_kwargs", {}))
