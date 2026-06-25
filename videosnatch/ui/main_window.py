# VideoSnatch main window - PyQt5 UI
import os
import logging
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLineEdit, QPushButton, QToolButton, QListWidget, QListWidgetItem,
    QLabel, QProgressBar, QStatusBar, QMenu, QAction, QFileDialog,
    QMessageBox, QDialog, QDialogButtonBox, QSpinBox, QFormLayout,
    QGroupBox, QFrame, QAbstractItemView, QComboBox,
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices

from ..interceptor import VideoInfo, CaptchaInfo
from ..auth_vault import AuthVault
from ..bookmark_importer import import_bookmarks_from_html, BookmarkParseError
from ..engine import BrowserEngine
from ..session import SessionManager

logger = logging.getLogger(__name__)

LIGHT_STYLE = """
QMainWindow, QWidget { background-color: #ffffff; color: #1a1a2e; font-size: 28px; }
QLineEdit { background-color: #f3f4f6; border: 1px solid #d1d5db;
    border-radius: 8px; padding: 12px 18px; color: #1a1a2e; }
QLineEdit:focus { border-color: #2563eb; }
QPushButton { background-color: #2563eb; color: #ffffff;
    border: none; border-radius: 8px; padding: 12px 24px; font-weight: bold; }
QPushButton:hover { background-color: #3b82f6; }
QPushButton:disabled { background-color: #e5e7eb; color: #9ca3af; }
QToolButton { background: transparent; border: none; border-radius: 6px;
    padding: 10px; color: #1a1a2e; }
QToolButton:hover { background: #f3f4f6; }
QListWidget { background-color: #f9fafb; border: 1px solid #e5e7eb;
    border-radius: 10px; padding: 6px; outline: none; }
QListWidget::item { background: #ffffff; border: 1px solid #e5e7eb;
    border-radius: 8px; padding: 12px; margin: 4px 6px; }
QListWidget::item:selected { border-color: #2563eb; }
QProgressBar { background: #e5e7eb; border: none; border-radius: 6px; height: 12px; }
QProgressBar::chunk { background: #16a34a; border-radius: 6px; }
QStatusBar { background: #f9fafb; border-top: 1px solid #e5e7eb; color: #6b7280; }
QGroupBox { border: 1px solid #e5e7eb; border-radius: 10px;
    margin-top: 16px; padding-top: 20px; font-weight: bold; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 8px; }
QMenu { background: #ffffff; color: #1a1a2e; border: 1px solid #e5e7eb; }
QMenu::item:selected { background: #f3f4f6; }
"""


class VideoItemWidget(QFrame):
    def __init__(self, video_info, parent=None):
        super().__init__(parent)
        self.video_info = video_info
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)

        info = QVBoxLayout()
        info.setSpacing(1)
        name = (video_info.filename[:42] + "...") if len(video_info.filename) > 45 else video_info.filename
        nl = QLabel(name)
        nl.setStyleSheet("color: #1a1a2e; font-weight: bold;")
        info.addWidget(nl)

        fmt = video_info.format_type
        label_map = {"hls": "HLS", "dash": "DASH", "embedded": "embedded", "direct": "direct"}
        details = label_map.get(fmt, fmt)
        if video_info.size > 0:
            details += f" | {video_info.size/1024/1024:.1f} MB"
        dl = QLabel(details)
        dl.setStyleSheet("color: #6b7280; font-size: 24px;")
        info.addWidget(dl)

        ul = QLabel(video_info.url[:50])
        ul.setStyleSheet("color: #9ca3af; font-size: 22px;")
        ul.setToolTip(video_info.url)
        info.addWidget(ul)

        layout.addLayout(info, 1)

        # 分辨率选择下拉框
        qcombo = QComboBox()
        qual_opts = ["Best", "2160p", "1080p", "720p", "480p", "360p"]
        for q in qual_opts:
            qcombo.addItem(q)
        current_q = getattr(video_info, "quality", "Best")
        if current_q in qual_opts:
            qcombo.setCurrentText(current_q)
        qcombo.setFixedWidth(60)
        qcombo.setStyleSheet(
            "QComboBox { font-size: 22px; padding: 1px 2px; "
            "border: 1px solid #d1d5db; border-radius: 3px; }"
        )
        layout.addWidget(qcombo)
        
        btn = QPushButton("DL")
        btn.setFixedSize(44, 34)
        btn.setStyleSheet(
            "QPushButton { background: #16a34a; color: #ffffff; "
            "border-radius: 4px; font-weight: bold; }"
            "QPushButton:hover { background: #22c55e; }"
        )
        btn.clicked.connect(self._on_dl)
        layout.addWidget(btn)

    def _on_dl(self):
        w = self.window()
        self.video_info.quality = self.findChild(QComboBox).currentText() if self.findChild(QComboBox) else "Best"
        if hasattr(w, "on_download_video"):
            w.on_download_video(self.video_info)


class DownloadItemWidget(QFrame):
    def __init__(self, url, filename, parent=None):
        super().__init__(parent)
        self.url = url
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)

        h = QHBoxLayout()
        n = filename[:47] + "..." if len(filename) > 50 else filename
        self.nl = QLabel(n)
        self.nl.setStyleSheet("color: #1a1a2e;")
        h.addWidget(self.nl, 1)

        self.cb = QPushButton("X")
        self.cb.setFixedSize(28, 28)
        self.cb.setStyleSheet(
            "QPushButton { background: transparent; color: #dc2626; "
            "border: none; font-weight: bold; }"
            "QPushButton:hover { color: #ef4444; }"
        )
        self.cb.clicked.connect(self._on_cancel)
        h.addWidget(self.cb)
        layout.addLayout(h)

        self.pb = QProgressBar()
        self.pb.setFixedHeight(6)
        layout.addWidget(self.pb)

        self.st = QLabel("waiting...")
        self.st.setStyleSheet("color: #6b7280; font-size: 22px;")
        layout.addWidget(self.st)

    def update_progress(self, pct, speed, dl_mb, total_mb):
        self.pb.setValue(int(pct))
        if speed > 0:
            self.st.setText(f"{dl_mb:.1f}/{total_mb:.1f} MB | {speed:.1f} MB/s")
        else:
            self.st.setText(f"{pct:.0f}%")

    def set_finished(self, _fp):
        self.pb.setValue(100)
        self.st.setText("Complete")
        self.cb.setVisible(False)

    def set_error(self, err):
        self.st.setText(f"Error: {err[:50]}")
        self.st.setStyleSheet("color: #dc2626; font-size: 22px;")

    def _on_cancel(self):
        w = self.window()
        if hasattr(w, "on_cancel_download"):
            w.on_cancel_download(self.url)


class SettingsDialog(QDialog):
    def __init__(self, download_dir, max_concurrent, ffmpeg_path, nm3u8dl_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VideoSnatch Settings")
        self.setMinimumWidth(560)
        self.setStyleSheet(LIGHT_STYLE)

        lo = QVBoxLayout(self)
        fm = QFormLayout()

        self.de = QLineEdit(download_dir)
        bb = QPushButton("Browse")
        bb.setFixedWidth(70)
        bb.clicked.connect(lambda: self._browse(self.de))
        hl = QHBoxLayout()
        hl.addWidget(self.de, 1)
        hl.addWidget(bb)
        fm.addRow("Download dir:", hl)

        self.sc = QSpinBox()
        self.sc.setRange(1, 10)
        self.sc.setValue(max_concurrent)
        fm.addRow("Max concurrent:", self.sc)

        self.fe = QLineEdit(ffmpeg_path or "")
        self.fe.setPlaceholderText("Auto-detect or browse to ffmpeg.exe")
        fb = QPushButton("Browse")
        fb.setFixedWidth(70)
        fb.clicked.connect(lambda: self._browse_file(self.fe))
        fl = QHBoxLayout()
        fl.addWidget(self.fe, 1)
        fl.addWidget(fb)
        fm.addRow("FFmpeg path:", fl)

        self.ne = QLineEdit(nm3u8dl_path or "")
        self.ne.setPlaceholderText("Path to N_m3u8DL-CLI.exe")
        nb = QPushButton("Browse")
        nb.setFixedWidth(70)
        nb.clicked.connect(lambda: self._browse_file_nm3u8(self.ne))
        nl = QHBoxLayout()
        nl.addWidget(self.ne, 1)
        nl.addWidget(nb)
        fm.addRow("N_m3u8DL-CLI:", nl)

        lo.addLayout(fm)
        bx = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bx.accepted.connect(self.accept)
        bx.rejected.connect(self.reject)
        lo.addWidget(bx)

    def _browse(self, field):
        p = QFileDialog.getExistingDirectory(self, "", field.text())
        if p:
            field.setText(p)

    def _browse_file(self, field):
        p, _ = QFileDialog.getOpenFileName(self, "Select ffmpeg.exe", "", "ffmpeg.exe;;All Files (*)")
        if p:
            field.setText(p)

    def _browse_file_nm3u8(self, field):
        p, _ = QFileDialog.getOpenFileName(self, "Select N_m3u8DL-CLI.exe",
                                           "", "N_m3u8DL-CLI.exe;;All Files (*)")
        if p:
            field.setText(p)

    def get_download_dir(self):
        return self.de.text()

    def get_max_concurrent(self):
        return self.sc.value()

    def get_ffmpeg_path(self):
        return self.fe.text().strip()

    def get_nm3u8dl_path(self):
        return self.ne.text().strip()


class MainWindow(QMainWindow):
    def __init__(self, engine, interceptor, download_manager):
        super().__init__()
        self._engine = engine
        self._interceptor = interceptor
        self._dm = download_manager
        self._dl_widgets = {}
        self._video_items = []
        self._browser_started = False
        self._session_mgr = SessionManager()
        self._session_mgr.set_engine(engine)
        self._auth_vault = AuthVault()

        self._setup_ui()
        self._connect_signals()
        self._refresh_session_list()
        self.setStyleSheet(LIGHT_STYLE)

    def _setup_ui(self):
        self.setWindowTitle("VideoSnatch - Video Download Browser")
        self.setMinimumSize(1000, 680)
        self.resize(1200, 800)

        c = QWidget()
        self.setCentralWidget(c)
        ml = QVBoxLayout(c)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # Toolbar
        tb = QWidget()
        tb.setFixedHeight(76)
        tb.setStyleSheet("background: #f9fafb; border-bottom: 1px solid #e5e7eb;")
        hl = QHBoxLayout(tb)
        hl.setContentsMargins(8, 4, 8, 4)

        nav_style = (
            "QToolButton { background: transparent; border: none; "
            "border-radius: 6px; padding: 10px; color: #1a1a2e; font-size: 26px; }"
            "QToolButton:hover { background: #e5e7eb; }"
            "QToolButton:disabled { color: #d1d5db; }"
        )

        self._bb = QToolButton()
        self._bb.setText("<")
        self._bb.setToolTip("Back")
        self._bb.setStyleSheet(nav_style)
        self._bb.clicked.connect(self._on_back)
        self._bb.setEnabled(False)

        self._bf = QToolButton()
        self._bf.setText(">")
        self._bf.setToolTip("Forward")
        self._bf.setStyleSheet(nav_style)
        self._bf.clicked.connect(self._on_forward)
        self._bf.setEnabled(False)

        self._br = QToolButton()
        self._br.setText("R")
        self._br.setToolTip("Refresh")
        self._br.setStyleSheet(nav_style)
        self._br.clicked.connect(self._on_refresh)
        self._br.setEnabled(False)

        self._bh = QToolButton()
        self._bh.setText("H")
        self._bh.setToolTip("Home")
        self._bh.setStyleSheet(nav_style)
        self._bh.clicked.connect(self._on_home)
        self._bh.setEnabled(False)

        for b in [self._bb, self._bf, self._br, self._bh]:
            hl.addWidget(b)

        self._url = QLineEdit()
        self._url.setPlaceholderText("Enter URL, e.g. https://www.youtube.com/watch?v=...")
        self._url.returnPressed.connect(self._on_go)
        hl.addWidget(self._url, 1)

        self._go = QPushButton("Go")
        self._go.setFixedHeight(52)
        self._go.clicked.connect(self._on_go)
        hl.addWidget(self._go)
        ml.addWidget(tb)

        # Splitter
        sp = QSplitter(Qt.Horizontal)

        # Left panel
        lw = QWidget()
        ll = QVBoxLayout(lw)
        ll.setContentsMargins(12, 12, 6, 12)

        sg = QGroupBox("Browser Control")
        sl = QVBoxLayout(sg)

        self._si = QLabel("Not started")
        self._si.setStyleSheet("color: #dc2626; font-size: 32px; font-weight: bold;")

        self._cul = QLabel("Current page: -")
        self._cul.setStyleSheet("color: #6b7280; font-size: 24px;")
        self._cul.setWordWrap(True)

        self._ptl = QLabel("Page title: -")
        self._ptl.setStyleSheet("color: #6b7280; font-size: 24px;")
        self._ptl.setWordWrap(True)

        self._bs = QPushButton("Start Browser")
        self._bs.setStyleSheet(
            "QPushButton { background: #16a34a; color: #ffffff; "
            "padding: 16px; font-size: 32px; font-weight: bold; border-radius: 10px; }"
            "QPushButton:hover { background: #22c55e; }"
        )
        self._bs.clicked.connect(self._on_start_browser)

        self._bscan = QPushButton("Scan Page")
        self._bscan.setEnabled(False)
        self._bscan.setStyleSheet(
            "QPushButton { background: #2563eb; color: #ffffff; "
            "padding: 12px; font-size: 28px; border-radius: 8px; }"
            "QPushButton:disabled { background: #e5e7eb; color: #9ca3af; }"
        )
        self._bscan.clicked.connect(self._on_scan)

        sl.addWidget(self._si)
        sl.addWidget(self._cul)
        sl.addWidget(self._ptl)
        sl.addWidget(self._bs)
        sl.addWidget(self._bscan)

        # 浏览器网页下载目录（下载 zip/exe 等文件的位置）
        bdl = QLabel("Browser downloads:")
        bdl.setStyleSheet("color: #6b7280; font-size: 24px; margin-top: 8px;")
        sl.addWidget(bdl)

        bdhl = QHBoxLayout()
        browser_dl_path = BrowserEngine.get_browser_download_dir() or self._dm.download_dir
        self._bdl_label = QLabel(browser_dl_path)
        self._bdl_label.setStyleSheet(
            "color: #1a1a2e; font-size: 22px; "
            "background: #f3f4f6; border-radius: 4px; padding: 6px 10px;"
        )
        self._bdl_label.setWordWrap(True)
        bdhl.addWidget(self._bdl_label, 1)

        self._bdl_open = QPushButton("Open")
        self._bdl_open.setFixedHeight(40)
        self._bdl_open.setStyleSheet(
            "QPushButton { background: transparent; color: #2563eb; "
            "border: 1px solid #2563eb; border-radius: 6px; padding: 6px 16px; "
            "font-size: 22px; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        self._bdl_open.clicked.connect(self._on_open_browser_dl_dir)
        bdhl.addWidget(self._bdl_open)
        sl.addLayout(bdhl)

        # Session 切换
        sh = QHBoxLayout()
        slh = QLabel("Session:")
        slh.setStyleSheet("color: #6b7280; font-size: 26px;")
        sh.addWidget(slh)
        self._scb = QComboBox()
        self._scb.setStyleSheet(
            "QComboBox { background: #f3f4f6; border: 1px solid #d1d5db; "
            "border-radius: 6px; padding: 8px 12px; font-size: 24px; }"
        )
        self._scb.currentIndexChanged.connect(self._on_session_switch)
        sh.addWidget(self._scb, 1)
        nsb = QPushButton("+")
        nsb.setFixedSize(36, 36)
        nsb.setStyleSheet(
            "QPushButton { background: #2563eb; color: #ffffff; "
            "border-radius: 4px; font-weight: bold; font-size: 28px; }"
        )
        nsb.clicked.connect(self._on_new_session)
        sh.addWidget(nsb)
        sl.addLayout(sh)

        ll.addWidget(sg)

        # CAPTCHA 状态
        cg = QGroupBox("CAPTCHA Status")
        cl = QVBoxLayout(cg)
        self._csl = QLabel("No CAPTCHA detected")
        self._csl.setStyleSheet("color: #16a34a; font-size: 26px;")
        cl.addWidget(self._csl)

        self._cl = QListWidget()
        self._cl.setMaximumHeight(120)
        self._cl.setStyleSheet(
            "QListWidget { background: #fff8e1; border: 1px solid #f59e0b; "
            "border-radius: 6px; }"
            "QListWidget::item { color: #92400e; padding: 4px; }"
        )
        cl.addWidget(self._cl)
        ll.addWidget(cg)

        tips = QLabel(
            "Tips:\n"
            "1. Click Start Browser to open CloakBrowser\n"
            "2. Browse any website in the browser\n"
            "3. Videos auto-detect in right panel\n"
            "4. Click DL button to download\n"
            "5. Browser downloads (zip/exe) go to the path above"
        )
        tips.setStyleSheet(
            "color: #6b7280; font-size: 24px; padding: 12px; "
            "background: #f3f4f6; border-radius: 8px;"
        )
        tips.setWordWrap(True)
        ll.addWidget(tips)
        ll.addStretch()
        sp.addWidget(lw)

        # Right panel
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(6, 12, 12, 12)

        vt = QLabel("Detected Videos")
        vt.setStyleSheet("font-size: 34px; font-weight: bold; color: #2563eb;")
        rl.addWidget(vt)

        vh = QHBoxLayout()
        self._vc = QLabel("0 videos")
        self._vc.setStyleSheet("color: #6b7280; font-size: 24px;")
        vh.addWidget(self._vc, 1)

        self._bda = QPushButton("Download All")
        self._bda.setFixedHeight(34)
        self._bda.setStyleSheet(
            "QPushButton { background: #7c3aed; color: #ffffff; "
            "font-size: 24px; padding: 8px 18px; border-radius: 6px; "
            "font-weight: bold; }"
            "QPushButton:disabled { background: #e5e7eb; color: #9ca3af; }"
        )
        self._bda.clicked.connect(self._on_download_all)
        self._bda.setEnabled(False)
        vh.addWidget(self._bda)

        cv = QPushButton("Clear")
        cv.setFixedHeight(44)
        cv.setStyleSheet(
            "QPushButton { background: transparent; color: #6b7280; "
            "font-size: 24px; padding: 8px 16px; "
            "border: 1px solid #d1d5db; border-radius: 6px; }"
            "QPushButton:hover { color: #dc2626; border-color: #dc2626; }"
        )
        cv.clicked.connect(self._on_clear)
        vh.addWidget(cv)
        rl.addLayout(vh)

        self._vl = QListWidget()
        self._vl.setContextMenuPolicy(Qt.CustomContextMenu)
        self._vl.customContextMenuRequested.connect(self._on_cm)
        rl.addWidget(self._vl, 1)

        dt = QLabel("Download Queue")
        dt.setStyleSheet("font-size: 34px; font-weight: bold; color: #2563eb;")
        rl.addWidget(dt)

        dh = QHBoxLayout()
        self._dlc = QLabel("0 active | 0 queue | 0 done")
        self._dlc.setStyleSheet("color: #6b7280; font-size: 24px;")
        dh.addWidget(self._dlc, 1)

        od = QPushButton("Open Dir")
        od.setFixedHeight(44)
        od.setStyleSheet(
            "QPushButton { background: transparent; color: #2563eb; "
            "font-size: 24px; padding: 8px 16px; "
            "border: 1px solid #2563eb; border-radius: 6px; }"
            "QPushButton:hover { background: #eff6ff; }"
        )
        od.clicked.connect(self._on_open_dir)
        dh.addWidget(od)
        rl.addLayout(dh)

        self._dll = QListWidget()
        rl.addWidget(self._dll)

        sp.addWidget(rw)
        sp.setStretchFactor(0, 7)
        sp.setStretchFactor(1, 3)
        sp.setSizes([700, 300])
        ml.addWidget(sp, 1)

        # Menus
        mb = self.menuBar()
        mb.setStyleSheet("background: #f9fafb; color: #1a1a2e; border: none;")

        sm = mb.addMenu("Session")
        nsa = QAction("New Session...", self)
        nsa.triggered.connect(self._on_new_session)
        sm.addAction(nsa)

        vm = mb.addMenu("Vault")
        vms = QAction("Save Cookies from Browser", self)
        vms.triggered.connect(self._on_save_cookies)
        vm.addAction(vms)
        vmr = QAction("Restore Cookies to Browser", self)
        vmr.triggered.connect(self._on_restore_cookies)
        vm.addAction(vmr)
        vm.addSeparator()
        vmi = QAction("Import Netscape Cookie File...", self)
        vmi.triggered.connect(self._on_import_cookies)
        vm.addAction(vmi)
        vme = QAction("Export Netscape Cookie File...", self)
        vme.triggered.connect(self._on_export_cookies)
        vm.addAction(vme)

        sm2 = mb.addMenu("Settings")
        sa = QAction("Preferences...", self)
        sa.triggered.connect(self._on_settings)
        sm2.addAction(sa)
        bm = mb.addMenu("Bookmarks")
        ba = QAction("Import Bookmarks from HTML...", self)
        ba.triggered.connect(self._on_import_bookmarks)
        bm.addAction(ba)
        bo = QAction("Open Bookmarks File Location", self)
        bo.triggered.connect(self._on_open_bookmarks_dir)
        bm.addAction(bo)

        hm = mb.addMenu("Help")
        aa = QAction("About VideoSnatch", self)
        aa.triggered.connect(self._on_about)
        hm.addAction(aa)

        self._sb = QStatusBar()
        self._sb.showMessage("Ready - click Start Browser to begin")
        self.setStatusBar(self._sb)

    def _connect_signals(self):
        e = self._engine
        e.page_changed.connect(self._on_page_changed)
        e.browser_ready.connect(self._on_browser_ready)
        e.browser_crashed.connect(self._on_browser_crashed)
        e.browser_closed.connect(self._on_browser_closed)
        e.navigation_error.connect(lambda m: self._sb.showMessage(f"Nav error: {m}"))
        e.page_console_message.connect(self._on_page_console_message)

        ic = self._interceptor
        ic.video_detected.connect(self._on_video_detected)
        ic.scan_finished.connect(lambda c: self._sb.showMessage(f"Scan done: {c} videos"))
        ic.captcha_detected.connect(self._on_captcha_detected)
        ic.captcha_resolved.connect(self._on_captcha_resolved)
        ic.download_requested.connect(self._on_page_download_requested)

        dm = self._dm
        dm.download_started.connect(self._on_dl_started)
        dm.download_progress.connect(self._on_dl_progress)
        dm.download_finished.connect(self._on_dl_finished)
        dm.download_error.connect(self._on_dl_error)
        dm.queue_changed.connect(self._update_dl_count)

        sm = self._session_mgr
        sm.session_changed.connect(self._on_session_changed)

    # --- Event handlers ---

    def _on_start_browser(self):
        self._si.setText("Starting browser...")
        self._si.setStyleSheet("color: #d97706; font-size: 32px; font-weight: bold;")
        self._bs.setEnabled(False)
        try:
            # 传入下载目录，使浏览器网页下载（zip/exe等）和视频下载目录统一
            ok = self._engine.start(download_dir=self._dm.download_dir)
            if ok:
                self._browser_started = True
            else:
                self._si.setText("Start failed")
                self._si.setStyleSheet("color: #dc2626; font-weight: bold;")
                self._bs.setEnabled(True)
        except Exception as e:
            logger.error(f"Start error: {e}")
            self._si.setText("Start failed")
            self._si.setStyleSheet("color: #dc2626; font-weight: bold;")
            self._bs.setEnabled(True)

    def _on_browser_ready(self):
        self._si.setText("Browser ready")
        self._si.setStyleSheet("color: #16a34a; font-weight: bold;")
        for b in [self._br, self._bh, self._bscan]:
            b.setEnabled(True)
        self._bs.setText("Restart Browser")
        self._bs.setEnabled(True)

        cdp = self._engine.get_cdp_session()
        page = self._engine.get_page()
        if cdp and page:
            self._interceptor.attach(cdp, page)
            import asyncio
            loop = self._engine._get_loop() if hasattr(self._engine, '_get_loop') else None
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._interceptor.start_intercepting(), loop
                )

        # 将浏览器上下文挂接到下载管理器，后台异步缓存 cookie
        ctx = self._engine._context
        loop = self._engine._get_loop() if hasattr(self._engine, '_get_loop') else None
        if ctx:
            self._dm.set_browser_context(ctx, loop)

        # 刷新浏览器下载目录显示（start() 中已通过 download_dir 更新 Preferences）
        dl_path = BrowserEngine.get_browser_download_dir()
        if dl_path:
            self._bdl_label.setText(dl_path)

        self._sb.showMessage("Browser ready - videos auto-detect as you browse")

    def _on_browser_crashed(self, msg):
        self._si.setText(f"Browser crashed: {msg}")
        self._si.setStyleSheet("color: #dc2626; font-weight: bold;")

    def _on_browser_closed(self):
        self._si.setText("Browser closed")
        self._si.setStyleSheet("color: #6b7280; font-weight: bold;")
        for b in [self._bb, self._bf, self._br, self._bh, self._bscan]:
            b.setEnabled(False)
        self._bs.setText("Start Browser")
        self._bs.setEnabled(True)
        self._browser_started = False

    def _on_page_changed(self, url):
        self._cul.setText(f"Current page: {url}")
        self._url.setText(url)
        self._bb.setEnabled(True)
        self._bf.setEnabled(True)
        self._interceptor.clear_detected()
        self._vl.clear()
        self._video_items = []
        self._vc.setText("0 videos")
        self._bda.setEnabled(False)
        title = self._engine.execute_js("document.title")
        if title:
            self._ptl.setText(f"Page title: {title}")

    def _on_go(self):
        url = self._url.text().strip()
        if url:
            self._engine.navigate(url)

    def _on_back(self):
        self._engine.go_back()

    def _on_forward(self):
        self._engine.go_forward()

    def _on_refresh(self):
        self._engine.refresh()

    def _on_home(self):
        self._engine.navigate("https://www.google.com")

    def _on_scan(self):
        self._sb.showMessage("Scanning page...")
        self._bscan.setEnabled(False)
        self._interceptor.clear_detected()
        page = self._engine.get_page()
        if page:
            from ..interceptor import PageScanner
            import asyncio
            loop = self._engine._get_loop() if hasattr(self._engine, '_get_loop') else None
            if loop and loop.is_running():
                f = asyncio.run_coroutine_threadsafe(
                    PageScanner.scan_page(page), loop
                )
                try:
                    for v in f.result(timeout=15):
                        self._on_video_detected(v)
                except Exception as e:
                    logger.error(f"Scan error: {e}")
        self._bscan.setEnabled(True)

    def _on_video_detected(self, vi):
        self._video_items.append(vi)
        item = QListWidgetItem(self._vl)
        w = VideoItemWidget(vi)
        item.setSizeHint(w.sizeHint())
        self._vl.addItem(item)
        self._vl.setItemWidget(item, w)
        self._vc.setText(f"{len(self._video_items)} videos")
        self._bda.setEnabled(True)

    def on_download_video(self, vi):
        self._dm.add_download(vi.to_dict())

    def on_cancel_download(self, url):
        self._dm.cancel(url)

    def _on_download_all(self):
        for v in self._video_items:
            self._dm.add_download(v.to_dict())

    def _on_page_console_message(self, text: str):
        """处理页面 console 消息（来自 Playwright 原生事件，比 CDP 更可靠）"""
        self._interceptor.process_console_text(text)

    def _on_page_download_requested(self, vi):
        """处理页面内浮动按钮发起的下载请求"""
        logger.info(f"页面请求下载: {vi.filename}")
        self.on_download_video(vi)
        self._sb.showMessage(f"Downloading: {vi.filename}", 3000)

    def _on_clear(self):
        self._interceptor.clear_detected()
        self._vl.clear()
        self._video_items = []
        self._vc.setText("0 videos")
        self._bda.setEnabled(False)

    def _on_dl_started(self, url, filename):
        item = QListWidgetItem(self._dll)
        w = DownloadItemWidget(url, filename)
        item.setSizeHint(w.sizeHint())
        self._dll.addItem(item)
        self._dll.setItemWidget(item, w)
        self._dl_widgets[url] = w

    def _on_dl_progress(self, url, pct, speed, dl_mb, total_mb):
        w = self._dl_widgets.get(url)
        if w:
            w.update_progress(pct, speed, dl_mb, total_mb)

    def _on_dl_finished(self, url, fp):
        w = self._dl_widgets.get(url)
        if w:
            w.set_finished(fp)
        self._sb.showMessage(f"Downloaded: {os.path.basename(fp)}", 5000)

    def _on_dl_error(self, url, err):
        w = self._dl_widgets.get(url)
        if w:
            w.set_error(err)
        self._sb.showMessage(f"Download failed: {err}", 5000)

    def _update_dl_count(self):
        a = self._dm.active_count()
        q = self._dm.queue_count()
        d = self._dm.completed_count()
        self._dlc.setText(f"{a} active | {q} queue | {d} done")

    def _on_open_dir(self):
        p = self._dm.download_dir
        if os.path.exists(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))

    def _on_open_browser_dl_dir(self):
        """打开浏览器网页下载目录（zip/exe等文件）"""
        p = BrowserEngine.get_browser_download_dir() or self._dm.download_dir
        if os.path.exists(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            self._sb.showMessage(f"已打开浏览器下载目录: {p}", 3000)
        else:
            self._sb.showMessage(f"下载目录不存在: {p}", 3000)

    def _on_cm(self, pos):
        item = self._vl.itemAt(pos)
        if not item:
            return
        w = self._vl.itemWidget(item)
        if not hasattr(w, "video_info"):
            return
        menu = QMenu(self)
        da = menu.addAction("Download")
        da.triggered.connect(lambda: self.on_download_video(w.video_info))
        ca = menu.addAction("Copy URL")
        ca.triggered.connect(lambda: self._copy_url(w.video_info.url))
        menu.exec_(self._vl.mapToGlobal(pos))

    def _copy_url(self, url):
        from PyQt5.QtWidgets import QApplication
        QApplication.clipboard().setText(url)
        self._sb.showMessage("URL copied", 2000)

    def _on_settings(self):
        d = SettingsDialog(
            self._dm.download_dir,
            self._dm._max_concurrent,
            self._dm.ffmpeg_path,
            self._dm.nm3u8dl_path,
            self,
        )
        if d.exec_() == QDialog.Accepted:
            self._dm.set_download_dir(d.get_download_dir())
            self._dm.set_max_concurrent(d.get_max_concurrent())
            fp = d.get_ffmpeg_path()
            if fp != self._dm.ffmpeg_path:
                self._dm.ffmpeg_path = fp
            np = d.get_nm3u8dl_path()
            if np != self._dm.nm3u8dl_path:
                self._dm.nm3u8dl_path = np

    def _on_about(self):
        QMessageBox.about(
            self,
            "About VideoSnatch",
            "<h2>VideoSnatch v1.0</h2>"
            "<p>Video download browser powered by CloakBrowser</p>"
            "<p>Engine: CloakBrowser (Stealth Chromium)</p>"
            "<p>Download: yt-dlp</p>"
            "<p>Detect & download videos from any website</p>"
            "<hr><p>MIT License</p>",
        )

    # ── Bookmarks 导入 ──────────────────────────

    def _get_profile_dir(self) -> str:
        """获取 CloakBrowser 的 Default profile 目录"""
        from pathlib import Path
        app_dir = Path(__file__).resolve().parent.parent.parent
        profile_dir = app_dir / "browser_profile" / "Default"
        profile_dir.mkdir(parents=True, exist_ok=True)
        return str(profile_dir)

    def _on_import_bookmarks(self):
        fp, _ = QFileDialog.getOpenFileName(
            self,
            "Import Bookmarks from HTML",
            "",
            "Bookmark Files (*.html *.htm);;All Files (*)",
        )
        if not fp:
            return

        profile_dir = self._get_profile_dir()
        try:
            result = import_bookmarks_from_html(fp, profile_dir)
            msg = f"✅ {result['message']}\
\
书签已写入: {result['filepath']}"
            if self._browser_started:
                msg += "\n\n⚠️ 浏览器正在运行，需要重启浏览器才能看到新书签。\
请点击 Restart Browser 或下次启动程序。"
            QMessageBox.information(self, "书签导入成功", msg)
            self._sb.showMessage(result["message"], 5000)
        except (BookmarkParseError, ValueError, IOError) as e:
            QMessageBox.warning(self, "导入失败", str(e))
            self._sb.showMessage(f"书签导入失败: {e}", 5000)
        except Exception as e:
            logger.exception("导入书签异常")
            QMessageBox.critical(self, "错误", f"导入书签时发生错误:\n{e}")
            self._sb.showMessage(f"书签导入错误: {e}", 5000)

    def _on_open_bookmarks_dir(self):
        """打开 Chrome 配置目录（用户可手动查看/编辑 Bookmarks 文件）"""
        profile_dir = self._get_profile_dir()
        if os.path.exists(profile_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(profile_dir))
            self._sb.showMessage(f"已打开书签目录: {profile_dir}", 3000)

    # ── Session 管理 ──────────────────────────────

    def _refresh_session_list(self):
        self._scb.blockSignals(True)
        self._scb.clear()
        sessions = self._session_mgr.list_sessions()
        for s in sessions:
            self._scb.addItem(f"{s.name} [{s.session_id[:8]}]", s.session_id)
        self._scb.blockSignals(False)
        active = self._session_mgr.get_active_session()
        if active:
            idx = self._scb.findData(active.session_id)
            if idx >= 0:
                self._scb.setCurrentIndex(idx)

    def _on_session_switch(self, idx):
        session_id = self._scb.itemData(idx)
        if session_id:
            self._session_mgr.activate_session(session_id)
            self._sb.showMessage(f"Switched to session: {session_id[:8]}", 3000)

    def _on_session_changed(self, old_id, new_id):
        self._refresh_session_list()
        if self._browser_started:
            self._sb.showMessage(f"Session switched, restart browser to apply", 5000)

    def _on_new_session(self):
        from PyQt5.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "New Session", "Session name:", text=f"Session {len(self._session_mgr.list_sessions()) + 1}")
        if ok and name.strip():
            sess = self._session_mgr.create_session(name.strip())
            self._refresh_session_list()

    # ── CAPTCHA 处理 ──────────────────────────────

    _DATA_ROLE = 32  # Qt.ItemDataRole.UserRole

    def _on_captcha_detected(self, ci):
        self._csl.setText(f"CAPTCHA: {ci.captcha_type}")
        self._csl.setStyleSheet("color: #dc2626; font-size: 26px; font-weight: bold;")
        item = QListWidgetItem(f"[{ci.captcha_type}] {ci.page_url[:50]}")
        item.setData(self._DATA_ROLE, id(ci))
        self._cl.addItem(item)
        self._sb.showMessage(f"CAPTCHA detected: {ci.captcha_type} — please complete it in the browser", 8000)

    def _on_captcha_resolved(self, ci):
        self._csl.setText("CAPTCHA resolved")
        self._csl.setStyleSheet("color: #16a34a; font-size: 26px;")
        for i in range(self._cl.count()):
            if self._cl.item(i).data(self._DATA_ROLE) == id(ci):
                self._cl.takeItem(i)
                break
        self._sb.showMessage("CAPTCHA resolved, continuing...", 3000)

    def _on_save_cookies(self):
        if not self._browser_started:
            self._sb.showMessage("Browser not started", 3000)
            return
        page = self._engine.get_page()
        if page:
            import asyncio
            loop = self._engine._get_loop()
            if loop:
                ctx = self._engine._context
                if ctx:
                    asyncio.run_coroutine_threadsafe(
                        self._auth_vault.extract_from_context(ctx), loop
                    )
                    self._sb.showMessage("Cookies saved to Auth Vault", 3000)

    def _on_restore_cookies(self):
        if not self._browser_started:
            self._sb.showMessage("Browser not started", 3000)
            return
        page = self._engine.get_page()
        if page:
            import asyncio
            loop = self._engine._get_loop()
            if loop:
                ctx = self._engine._context
                if ctx:
                    asyncio.run_coroutine_threadsafe(
                        self._auth_vault.inject_to_context(ctx), loop
                    )
                    self._sb.showMessage("Cookies restored from Auth Vault", 3000)

    def _on_import_cookies(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Import Netscape Cookie File", "",
            "Cookie Files (*.txt *.cookies);;All Files (*)"
        )
        if fp:
            count = self._auth_vault.import_netscape(fp)
            self._sb.showMessage(f"Imported {count} cookies from {os.path.basename(fp)}", 5000)

    def _on_export_cookies(self):
        fp, _ = QFileDialog.getSaveFileName(
            self, "Export Netscape Cookie File", "cookies.txt",
            "Cookie Files (*.txt);;All Files (*)"
        )
        if fp:
            count = self._auth_vault.export_netscape(fp)
            self._sb.showMessage(f"Exported {count} cookies to {os.path.basename(fp)}", 5000)

    def closeEvent(self, event):
        logger.info("Closing VideoSnatch...")
        if self._browser_started:
            self._engine.stop()
        event.accept()
