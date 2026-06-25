"""VideoSnatch - 基于 CloakBrowser 的全能视频下载浏览器

启动命令:
    python main.py

依赖:
    pip install cloakbrowser PyQt5 yt-dlp
"""

import sys
import os
import logging

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
from PyQt5.QtCore import Qt

from videosnatch.engine import BrowserEngine
from videosnatch.interceptor import VideoInterceptor
from videosnatch.downloader import DownloadManager
from videosnatch.ui.main_window import MainWindow


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 减少嘈杂的日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    app = QApplication(sys.argv)
    app.setApplicationName("VideoSnatch")
    app.setOrganizationName("VideoSnatch")

    # 启用高 DPI 缩放支持（适配电视等高分辨率大屏）
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    # 使用系统默认字体（尊重 Windows 显示缩放设置）
    font = app.font()
    font.setFamilies(["Segoe UI", "Microsoft YaHei", "sans-serif"])
    font.setPointSize(16)
    app.setFont(font)

    app.setStyleSheet(f"QToolTip {{ font-size: {font.pointSize()}pt; }}")

    logger.info("Initializing VideoSnatch components...")

    # 初始化组件
    engine = BrowserEngine()
    interceptor = VideoInterceptor()
    download_manager = DownloadManager()

    # 创建主窗口
    window = MainWindow(engine, interceptor, download_manager)
    window.show()

    logger.info("VideoSnatch started")

    # 运行应用
    exit_code = app.exec_()

    # 清理
    logger.info("Shutting down...")
    if engine.is_running():
        engine.stop()

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
