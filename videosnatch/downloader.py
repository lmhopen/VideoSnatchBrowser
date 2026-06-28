"""VideoSnatch Download Manager - 视频下载管理"""

import os
import re
import time
import logging
import subprocess
import json
from pathlib import Path
from urllib.parse import urlparse

from PyQt5.QtCore import QObject, pyqtSignal, QThread, QTimer

logger = logging.getLogger(__name__)


def get_default_download_dir():
    home = Path.home()
    candidates = [
        Path("E:/") / "VideoSnatch",
        home / "Videos" / "VideoSnatch",
        home / "Downloads" / "VideoSnatch",
        Path(os.environ.get("TEMP", ".")) / "VideoSnatch",
    ]
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            return str(d)
        except Exception:
            continue
    return str(Path.cwd() / "VideoSnatchDownloads")


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    if len(name) > 200:
        base, ext = os.path.splitext(name)
        name = base[:190] + ext
    return name or "video.mp4"


def find_ffmpeg() -> str:
    """自动查找 ffmpeg，优先 PATH / where 命令，再扫描常见安装路径
    返回前运行 ffmpeg -version 验证是否真的可用。"""
    import shutil

    def _validate(fp: str) -> str:
        """确认 ffmpeg 文件存在且能正常运行"""
        if not fp or not os.path.isfile(fp):
            return ""
        try:
            r = subprocess.run([fp, "-version"], capture_output=True, timeout=8)
            if r.returncode == 0:
                return fp
            logger.debug(f"ffmpeg 验证失败 (exit={r.returncode}): {fp}")
        except Exception as e:
            logger.debug(f"ffmpeg 验证异常: {e}")
        return ""

    # 1. PATH 环境变量
    fp = _validate(shutil.which("ffmpeg"))
    if fp:
        return fp

    # 2. Windows where 命令（查找更广泛）
    try:
        result = subprocess.run(
            ["where", "ffmpeg"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                fp = _validate(line.strip())
                if fp:
                    return fp
    except Exception:
        pass

    # 3. 常见安装目录扫描
    scan_dirs = [
        "C:\\ffmpeg\\bin",
        "C:\\Program Files\\ffmpeg\\bin",
        "C:\\Program Files\\ffmpeg",
        "D:\\ffmpeg\\bin",
        "E:\\ffmpeg\\bin",
        # N_m3u8DL-CLI 捆绑的 ffmpeg（常见路径）
        "D:\\Program Files\\N_m3u8DL-CLI_v3.0.2_with_ffmpeg_and_SimpleG",
        "D:\\Program Files\\N_m3u8DL-CLI_v3.0.2_with_ffmpeg_and_SimpleG\\Downloads",
        "E:\\迅雷下载",
    ]
    for d in scan_dirs:
        for name in ["ffmpeg.exe", os.path.join("bin", "ffmpeg.exe")]:
            fp = _validate(os.path.join(d, name))
            if fp:
                return fp

    # 4. 扫描 %USERPROFILE% 下常见位置（scoop、chocolatey 等）
    user_home = os.path.expanduser("~")
    profile_scan = [
        os.path.join(user_home, "scoop", "apps", "ffmpeg", "current", "bin"),
        os.path.join(user_home, "AppData", "Local", "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*"),
    ]
    for base in profile_scan:
        if "*" in base:
            import glob
            for matched in glob.glob(base):
                fp = _validate(os.path.join(matched, "ffmpeg.exe"))
                if fp:
                    return fp
        else:
            fp = _validate(os.path.join(base, "ffmpeg.exe"))
            if fp:
                return fp

    return ""


def find_nm3u8dl() -> str:
    """查找 N_m3u8DL-CLI 可执行文件"""
    import shutil
    fp = shutil.which("N_m3u8DL-CLI")
    if fp:
        return fp

    candidates = [
        "D:\\Program Files\\N_m3u8DL-CLI_v3.0.2_with_ffmpeg_and_SimpleG\\N_m3u8DL-CLI_v3.0.2.exe",
        "D:\\Program Files\\N_m3u8DL-CLI_v3.0.2_with_ffmpeg_and_SimpleG\\N_m3u8DL-CLI-SimpleG.exe",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    return ""


class DownloadWorker(QThread):
    """在后台线程中执行下载"""

    progress = pyqtSignal(str, float, float, float, float)
    finished = pyqtSignal(str, str)
    error = pyqtSignal(str, str)
    log = pyqtSignal(str, str)

    def __init__(self, video_info: dict, download_dir: str, parent=None):
        super().__init__(parent)
        self.video_info = video_info
        self.download_dir = download_dir
        self._cancelled = False
        self._original_url = video_info.get("url", "")
        self._nm3u8dl_path = ""
        self._cookies_file = ""

    def cancel(self):
        self._cancelled = True

    def _cleanup(self):
        if self._cookies_file and os.path.isfile(self._cookies_file):
            try:
                os.remove(self._cookies_file)
                self._cookies_file = ""
            except Exception:
                pass

    def _try_get_page_title(self) -> str:
        """尝试从页面 URL 提取标题，用于文件名
        仅用 HTML <title> 解析（无关平台通用，不触发 yt-dlp 避免速率限制）。
        yt-dlp 实际下载时 outtmpl 自动用 %(title)s 命名文件，不需提前提取。"""
        page_url = self.video_info.get("page_url", "")
        if not page_url:
            return ""

        try:
            import httpx
            resp = httpx.get(page_url, timeout=10, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            if resp.status_code == 200:
                import re
                m = re.search(r'<title[^>]*>(.*?)</title>', resp.text, re.I | re.S)
                if m:
                    title = m.group(1).strip()
                    # 清理多余空白
                    title = re.sub(r'\s+', ' ', title)
                    if title and len(title) > 1:
                        logger.info(f"[HTML] 获取到标题: {title[:60]}")
                        return title
        except Exception:
            pass

        return ""

    def run(self):
        url = self.video_info.get("url", "")
        page_url = self.video_info.get("page_url", "")
        page_url = self._normalize_platform_url(page_url)
        fmt = self.video_info.get("format", "direct")
        is_ts = url.lower().rstrip("?&#").endswith(".ts")

        if self._cancelled:
            self._cleanup()
            return

        # ★ 先尝试提取页面标题作为文件名（影响直接下载和 N_m3u8DL-CLI 路径）
        page_title = self._try_get_page_title()
        if page_title:
            safe_title = sanitize_filename(page_title) + ".mp4"
            self.video_info["filename"] = safe_title

        logger.info(f"下载开始 fmt={fmt} ts={is_ts} original={url[:80]} page={page_url[:80] if page_url else ''}")

        try:
            if fmt in ("hls", "dash", "embedded"):
                if self._nm3u8dl_path and (url.lower().rstrip("?&#").endswith(".m3u8") or "m3u8" in url.lower()):
                    if self._download_with_nm3u8dl(url):
                        return
                    logger.info("N_m3u8DL-CLI 失败，回退到 yt-dlp")
                if page_url and self._is_known_platform(page_url):
                    self._download_with_ytdlp(page_url)
                else:
                    self._download_with_ytdlp(url)
            elif fmt == "direct":
                if is_ts and page_url:
                    self._download_with_ytdlp(page_url)
                elif page_url and self._is_known_platform(page_url):
                    self._download_with_ytdlp(page_url)
                else:
                    self._download_with_ytdlp(url)
            else:
                self._download_with_ytdlp(url)
        finally:
            self._cleanup()

    @staticmethod
    def _is_ts_url(url: str) -> bool:
        from urllib.parse import urlparse
        path = urlparse(url).path.lower()
        return path.endswith(".ts")

    @staticmethod
    def _is_known_platform(url: str) -> bool:
        domains = [
            "youtube.com", "youtu.be", "googlevideo.com",
            "bilibili.com", "b23.tv",
            "twitter.com", "x.com",
            "tiktok.com",
            "instagram.com",
            "facebook.com", "fb.com",
            "vimeo.com",
            "dailymotion.com",
            "twitch.tv",
            "qq.com",
            "douyin.com",
        ]
        from urllib.parse import urlparse
        netloc = urlparse(url).netloc.lower()
        return any(d in netloc for d in domains)

    @staticmethod
    def _normalize_platform_url(url: str) -> str:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
        parsed = urlparse(url)
        if "douyin.com" in parsed.netloc.lower() and parsed.path.rstrip("/") == "/jingxuan":
            params = parse_qs(parsed.query)
            modal_ids = params.get("modal_id", [])
            if modal_ids:
                new_path = f"/video/{modal_ids[0]}"
                new_parsed = parsed._replace(path=new_path, query="")
                normalized = urlunparse(new_parsed)
                logger.info(f"抖音 URL 已转换: {url[:80]} -> {normalized[:80]}")
                return normalized
        return url

    def _download_with_ytdlp(self, url: str):
        logger.info(f"yt-dlp 处理 URL: {url[:100]}")
        try:
            import yt_dlp
            # 注册 EJS 远程组件，用于 YouTube JS challenge 求解
            # （API 模式下不会自动注册，需要手动添加）
            from yt_dlp.globals import supported_remote_components
            if "ejs:github" not in supported_remote_components.value:
                supported_remote_components.value.append("ejs:github")
        except ImportError:
            self.error.emit(self._original_url, "yt-dlp 未安装，请运行: pip install yt-dlp")
            return

        output_template = os.path.join(self.download_dir, "%(title).100s - %(id)s.%(ext)s")

        progress_hooks = []

        def progress_hook(d):
            if self._cancelled:
                raise Exception("下载已取消")
            emit_url = self._original_url

            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes", 0)
                pct = (downloaded / total * 100) if total > 0 else 0
                speed = d.get("speed", 0) or 0
                speed_mb = speed / 1024 / 1024 if speed else 0
                downloaded_mb = downloaded / 1024 / 1024
                total_mb = total / 1024 / 1024 if total else 0
                self.progress.emit(emit_url, pct, speed_mb, downloaded_mb, total_mb)

            elif d.get("status") == "finished":
                self.progress.emit(emit_url, 100, 0, 0, 0)

        progress_hooks.append(progress_hook)

        page_url = self.video_info.get("page_url", "")
        parsed = urlparse(page_url) if page_url else None
        referer = f"{parsed.scheme}://{parsed.netloc}/" if (parsed and parsed.netloc) else ""

        # ★ 根据选择的分辨率使用 format_spec
        # 优先合并视频+音频（解决 YouTube DASH 流音视频分离的问题），
        # 回退到单文件 mp4，再回退到任意单文件
        quality = self.video_info.get("quality", "Best")
        q = str(quality).lower().rstrip("p")
        # 有 ffmpeg = 可以合并 video+audio
        has_ffmpeg = bool(getattr(self, '_ffmpeg_path', None))
        if has_ffmpeg:
            # 指定 ext=mp4/m4a 确保音视频都能无损复制进 mp4 容器
            # （避免 opus/webm 音频需要转码的问题）
            if q == "best" or q == "":
                format_spec = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            else:
                try:
                    h = int(q)
                    format_spec = f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/best[height<={h}][ext=mp4]/best[height<={h}]/best"
                except ValueError:
                    format_spec = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            # 没有 ffmpeg 时只能用单文件
            if q == "best" or q == "":
                format_spec = "best[ext=mp4]/best"
            else:
                try:
                    h = int(q)
                    format_spec = f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
                except ValueError:
                    format_spec = "best[ext=mp4]/best"

        ydl_opts = {
            "format": format_spec,
            "outtmpl": output_template,
            "progress_hooks": progress_hooks,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "no_color": True,
            "extractor_retries": 5,
            "retries": 15,
            "fragment_retries": 15,
            "continuedl": True,
            "concurrent_fragment_downloads": 10,
            "socket_timeout": 30,
            "noplaylist": True,
            "throttledratelimit": 102400,
            "sleep_interval_requests": 0.5,
            "cookiefile": self._cookies_file or None,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": referer or url,
            },
        }
        # 有 ffmpeg 时启用音视频合并
        if has_ffmpeg:
            ydl_opts["merge_output_format"] = "mp4"
            ydl_opts["ffmpeg_location"] = self._ffmpeg_path
            ydl_opts["postprocessor_args"] = {
                "ffmpeg": ["-c", "copy"],
            }

        # YouTube 需要 JS runtime + EJS 组件来解密签名和 n-challenge
        if self._is_known_platform(url):
            ydl_opts["remote_components"] = ["ejs:github"]
            # 启用 Node.js 作为 JS challenge 求解器
            ydl_opts["js_runtimes"] = {"node": {}}

        emit_url = self._original_url
        fmt = self.video_info.get("format", "direct")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info and not self._cancelled:
                    # 1. 先尝试从 yt-dlp 报告的路径找
                    filepath = (
                        info.get("requested_downloads", [{}])[0].get("filepath")
                        or info.get("filepath")
                        or self._resolve_output_path(ydl, info)
                    )
                    # 2. 如果报告路径不存在，全目录扫描最近创建的视频文件
                    #    （yt-dlp bestvideo+bestaudio 合并后可能改变文件名）
                    if not filepath or not os.path.isfile(filepath):
                        scanned = self._find_recent_video(self.download_dir)
                        if scanned:
                            logger.info(f"通过全目录扫描找到下载文件: {scanned}")
                            filepath = scanned
                    if filepath and os.path.isfile(filepath):
                        logger.info(f"下载完成: {filepath}")
                        self.finished.emit(emit_url, filepath)
                    else:
                        logger.warning(f"下载后找不到文件，已扫描目录: {self.download_dir}")
                        self.error.emit(emit_url, "下载完成但找不到文件")
                elif self._cancelled:
                    self.error.emit(emit_url, "下载已取消")
                elif fmt == "direct":
                    logger.warning(f"yt-dlp 提取信息为空, 回退到直接下载原始 URL: {emit_url[:80]}")
                    self._download_direct(emit_url)
                elif self._is_ts_url(self._original_url):
                    logger.warning(f"yt-dlp 无法解析页面, 回退到直接下载 ts: {self._original_url[:80]}")
                    self._download_direct(emit_url)
                else:
                    logger.warning(f"yt-dlp 无法获取视频信息 (url={url[:80]}, page={page_url[:80]})")
                    self.error.emit(emit_url, "无法获取视频信息")
        except Exception as e:
            if self._cancelled:
                return
            err_msg = str(e)
            logger.warning(f"yt-dlp 异常: {err_msg[:200]}")
            # 对于已知平台（YouTube/B站等），不回退直接下载（那会下载 HTML 页面）
            page_url = self.video_info.get("page_url", "")
            if fmt == "direct" and not self._is_known_platform(page_url):
                logger.info("yt-dlp 异常, 回退到直接下载")
                self._download_direct(emit_url)
            elif "HTTP Error" in err_msg:
                self.error.emit(emit_url, f"HTTP 错误: {err_msg}")
            elif "Video unavailable" in err_msg:
                self.error.emit(emit_url, "视频不可用或需要登录")
            elif "Private video" in err_msg:
                self.error.emit(emit_url, "视频为私密")
            else:
                self.error.emit(emit_url, f"下载失败: {err_msg}")

    def _resolve_output_path(self, ydl, info) -> str:
        try:
            expected = ydl.prepare_filename(info)
        except Exception:
            expected = ""
        ext = info.get("ext", "mp4")
        if expected and not expected.endswith(f".{ext}"):
            expected = f"{expected}.{ext}"

        candidates = []
        if expected:
            candidates.append(expected)
            joined = os.path.join(self.download_dir, os.path.basename(expected))
            candidates.append(joined)
            stem = os.path.splitext(os.path.basename(expected))[0]
        else:
            stem = ""

        for p in candidates:
            if os.path.isfile(p):
                logger.info(f"找到下载文件: {p}")
                return p

        if stem:
            for root, dirs, files in os.walk(self.download_dir):
                for f in files:
                    if f.startswith(stem) and os.path.isfile(os.path.join(root, f)):
                        fp = os.path.join(root, f)
                        logger.info(f"通过文件名匹配找到: {fp}")
                        return fp

        # 扫描下载目录中最近创建的视频文件（时间窗口 = 最近 60 秒）
        before = time.time()
        video_exts = (".mp4", ".mkv", ".webm", ".flv", ".mov", ".avi", ".ts", ".m4a", ".m4v")
        newest = None
        newest_time = 0
        for root, dirs, files in os.walk(self.download_dir):
            for f in files:
                if f.lower().endswith(video_exts):
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                    except OSError:
                        continue
                    if mtime > newest_time and mtime > before - 300:
                        newest = fp
                        newest_time = mtime

        if newest:
            logger.info(f"通过时间扫描找到下载文件: {newest}")
            return newest

        fallback = joined if expected else os.path.join(self.download_dir, "video.mp4")
        logger.warning(f"未找到下载文件，返回: {fallback}")
        return fallback

    def _download_direct(self, url: str):
        import httpx

        output_path = os.path.join(self.download_dir, sanitize_filename(
            self.video_info.get("filename", "video.mp4")
        ))

        try:
            with httpx.Client(follow_redirects=True, timeout=60) as client:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        self.error.emit(url, f"HTTP {response.status_code}")
                        return

                    ct = response.headers.get("content-type", "")
                    if ct and not ct.startswith("video/"):
                        logger.warning(f"非视频 Content-Type: {ct}，仍尝试下载")

                    total = int(response.headers.get("content-length", 0))
                    if 0 < total < 65536:
                        self.error.emit(url, f"文件过小 ({total} bytes)，非有效视频")
                        return

                    downloaded = 0
                    chunk_size = 8192

                    with open(output_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size):
                            if self._cancelled:
                                f.close()
                                os.remove(output_path)
                                return
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = downloaded / total * 100
                                mb_dl = downloaded / 1024 / 1024
                                mb_total = total / 1024 / 1024
                                self.progress.emit(url, pct, 0, mb_dl, mb_total)

            if not self._cancelled:
                self.finished.emit(url, output_path)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(url, f"下载失败: {str(e)}")

    def _download_with_nm3u8dl(self, url: str) -> bool:
        """使用 N_m3u8DL-CLI 下载 m3u8 流，返回 True 表示成功"""
        nm3u8dl = self._nm3u8dl_path
        if not nm3u8dl or not os.path.isfile(nm3u8dl):
            nm3u8dl = find_nm3u8dl()
            if not nm3u8dl:
                logger.warning("N_m3u8DL-CLI not found, fallback to yt-dlp")
                return False

        emit_url = self._original_url

        save_name = sanitize_filename(self.video_info.get("filename", "video"))
        for ext in ('.mp4', '.mkv', '.ts', '.webm', '.m3u8'):
            if save_name.lower().endswith(ext):
                save_name = save_name[:-len(ext)]
                break
        if not save_name:
            import time as ttime
            save_name = f"video_{int(ttime.time())}"

        tmp_dir = os.path.join(self.download_dir, f".tmp_{save_name}")
        os.makedirs(tmp_dir, exist_ok=True)

        parsed_page = urlparse(self.video_info.get("page_url", ""))
        referer = f"{parsed_page.scheme}://{parsed_page.netloc}/" if parsed_page.netloc else ""
        headers_parts = [
            "User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept:*/*",
            "Accept-Language:zh-CN,zh;q=0.9,en;q=0.8",
        ]
        if referer:
            headers_parts.append(f"Referer:{referer}")
        headers_str = "|".join(headers_parts)

        cmd = [
            nm3u8dl, url,
            "--workDir", tmp_dir,
            "--saveName", save_name,
            "--headers", headers_str,
            "--enableDelAfterDone",
            "--enableBinaryMerge",
            "--enableMuxFastStart",
            "--retryCount", "15",
            "--timeOut", "30",
            "--maxThreads", "16",
            "--minThreads", "8",
            "--noProxy",
        ]

        logger.info(f"N_m3u8DL-CLI starting for {url[:80]}...")
        self.progress.emit(emit_url, 10, 0, 0, 0)
        try:
            startupinfo = None
            createflags = 0
            if hasattr(subprocess, 'STARTUPINFO'):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            if hasattr(subprocess, 'CREATE_NO_WINDOW'):
                createflags = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                startupinfo=startupinfo,
                creationflags=createflags,
            )

            import shutil as shu

            # 用 communicate 避免管道死锁（控制台 \r 进度会导致 readline 死锁）
            self.progress.emit(emit_url, 30, 0, 0, 0)
            try:
                stdout_data, _ = process.communicate(timeout=1200)
            except subprocess.TimeoutExpired:
                logger.error("N_m3u8DL-CLI timed out after 1200s")
                process.kill()
                process.wait(timeout=10)
                shu.rmtree(tmp_dir, ignore_errors=True)
                return False

            if self._cancelled:
                shu.rmtree(tmp_dir, ignore_errors=True)
                return False

            # 记录 N_m3u8DL-CLI 输出（截断以防过大）
            if stdout_data:
                out_text = stdout_data.decode('utf-8', errors='replace')[:3000]
                for line in out_text.split('\n'):
                    line = line.strip()
                    if line:
                        logger.debug(f"N_m3u8DL-CLI: {line[:200]}")

            if process.returncode not in (0, 1) and process.returncode is not None:
                logger.warning(f"N_m3u8DL-CLI exit code: {process.returncode}")
                shu.rmtree(tmp_dir, ignore_errors=True)
                return False

            self.progress.emit(emit_url, 70, 0, 0, 0)
            output_file = self._locate_nm3u8dl_output(tmp_dir, save_name)
            if not output_file:
                output_file = self._find_recent_video(tmp_dir, save_name)

            if output_file and os.path.isfile(output_file):
                dest = os.path.join(self.download_dir, os.path.basename(output_file))
                if os.path.normpath(output_file) != os.path.normpath(dest):
                    if os.path.isfile(dest):
                        os.remove(dest)
                    shu.move(output_file, dest)
                logger.info(f"N_m3u8DL-CLI done: {dest}")
                self.progress.emit(emit_url, 100, 0, 0, 0)
                self.finished.emit(emit_url, dest)
                shu.rmtree(tmp_dir, ignore_errors=True)
                return True

            # 最终回退：扫描下载目录中刚刚产生的视频文件
            logger.info("N_m3u8DL-CLI output not found in tmp_dir, scanning download_dir...")
            fallback = self._find_recent_video(self.download_dir)
            if fallback and os.path.isfile(fallback):
                logger.info(f"N_m3u8DL-CLI done (found in download_dir): {fallback}")
                self.progress.emit(emit_url, 100, 0, 0, 0)
                self.finished.emit(emit_url, fallback)
                shu.rmtree(tmp_dir, ignore_errors=True)
                return True

            logger.warning("N_m3u8DL-CLI output file not found")
            shu.rmtree(tmp_dir, ignore_errors=True)
            return False

        except Exception as e:
            logger.error(f"N_m3u8DL-CLI error: {e}")
            import shutil as shu
            shu.rmtree(tmp_dir, ignore_errors=True)
            return False

    def _locate_nm3u8dl_output(self, work_dir: str, save_name: str) -> str:
        candidates = []
        for ext in ('.mp4', '.ts', '.mkv', '.webm'):
            candidates.append(os.path.join(work_dir, f"{save_name}{ext}"))
        for root, dirs, files in os.walk(work_dir):
            for f in files:
                fp = os.path.join(root, f)
                if f.lower().endswith(('.mp4', '.ts', '.mkv', '.webm')):
                    if f.startswith(save_name) or f == f"{save_name}.mp4":
                        return fp
        for c in candidates:
            if os.path.isfile(c):
                return c
        return ""

    @staticmethod
    def _find_recent_video(directory: str, save_name: str = "") -> str:
        newest = None
        newest_time = 0
        video_exts = ('.mp4', '.ts', '.mkv', '.webm')
        now = time.time()
        for root, dirs, files in os.walk(directory):
            for f in files:
                if f.lower().endswith(video_exts):
                    if save_name and save_name not in f:
                        continue
                    fp = os.path.join(root, f)
                    try:
                        mtime = os.path.getmtime(fp)
                    except OSError:
                        continue
                    if mtime > newest_time and mtime > now - 600:
                        newest = fp
                        newest_time = mtime
        return newest or ""


class DownloadManager(QObject):
    """管理视频下载队列和进度"""

    download_started = pyqtSignal(str, str)
    download_progress = pyqtSignal(str, float, float, float, float)
    download_finished = pyqtSignal(str, str)
    download_error = pyqtSignal(str, str)
    queue_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.download_dir = get_default_download_dir()
        self._active_workers: dict[str, DownloadWorker] = {}
        self._queue: list[dict] = []
        self._completed: list[dict] = []
        self._max_concurrent = 3
        self._ffmpeg_path = find_ffmpeg()
        self._nm3u8dl_path = find_nm3u8dl()
        self._browser_context = None
        self._browser_loop = None

    @property
    def ffmpeg_path(self):
        return self._ffmpeg_path

    @ffmpeg_path.setter
    def ffmpeg_path(self, path: str):
        if path and not os.path.isfile(path):
            path = find_ffmpeg()
        self._ffmpeg_path = path

    @property
    def nm3u8dl_path(self):
        return self._nm3u8dl_path

    @nm3u8dl_path.setter
    def nm3u8dl_path(self, path: str):
        if path and not os.path.isfile(path):
            path = find_nm3u8dl()
        self._nm3u8dl_path = path

    def set_download_dir(self, path: str):
        self.download_dir = path
        Path(path).mkdir(parents=True, exist_ok=True)

    def set_max_concurrent(self, n: int):
        self._max_concurrent = max(1, n)

    def set_browser_context(self, context, loop=None):
        """设置浏览器上下文，同时异步缓存 cookie（不阻塞主线程）"""
        self._browser_context = context
        self._browser_loop = loop
        if context and loop:
            import asyncio
            asyncio.run_coroutine_threadsafe(
                self._async_cache_cookies(context), loop
            )

    def _ensure_cookies(self) -> str:
        """在下载前确保已缓存最新的 cookies（通过 CDP 从浏览器上下文提取，不碰锁定的 SQLite）"""
        cached = self._get_cookies_file()
        # 如果已有缓存文件且浏览器已关闭，直接用缓存
        if cached and os.path.isfile(cached) and not self._browser_context:
            return cached
        # 如果有浏览器上下文，重新导出最新 cookies
        if self._browser_context and self._browser_loop:
            import asyncio
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._async_cache_cookies(self._browser_context),
                    self._browser_loop
                )
                future.result(timeout=15)
            except Exception as e:
                logger.debug(f"下载前刷新 cookies 失败（可能浏览器已关闭）: {e}")
        return self._get_cookies_file()

    async def _async_cache_cookies(self, context):
        """后台异步提取 cookie 并缓存到临时文件"""
        try:
            cookies = await context.cookies()
        except Exception as e:
            logger.debug(f"提取 cookie 失败: {e}")
            return
        if not cookies:
            return
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="vsnatch_cookies_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# Generated by VideoSnatch\n\n")
            for c in cookies:
                domain = c.get("domain", "")
                name = c.get("name", "")
                value = c.get("value", "")
                path_c = c.get("path", "/")
                secure = "TRUE" if c.get("secure") else "FALSE"
                domain_flag = "TRUE" if domain.startswith(".") else "FALSE"
                expires = int(c.get("expires", 0)) or 0
                f.write(f"{domain}\t{domain_flag}\t{path_c}\t{secure}\t{expires}\t{name}\t{value}\n")
        old = getattr(self, '_cookies_cache_file', '')
        self._cookies_cache_file = path
        if old and os.path.isfile(old):
            try:
                os.remove(old)
            except Exception:
                pass
        logger.info(f"已缓存 {len(cookies)} 个 cookie")

    def _get_cookies_file(self) -> str:
        """返回缓存 cookie 文件路径（不阻塞主线程）"""
        return getattr(self, '_cookies_cache_file', '')

    def add_download(self, video_info: dict):
        url = video_info.get("url", "")
        if not url:
            return

        if url in self._active_workers:
            logger.info(f"已在下载中: {url}")
            return

        if len(self._active_workers) >= self._max_concurrent:
            self._queue.append(video_info)
            self.queue_changed.emit()
            logger.info(f"已加入队列: {video_info.get('filename', url)}")
            return

        self._start_download(video_info)

    def download_all(self, videos: list[dict]):
        for v in videos:
            self.add_download(v)

    def cancel(self, url: str):
        worker = self._active_workers.pop(url, None)
        if worker:
            worker.cancel()
            worker.wait(2000)
            self.queue_changed.emit()

    def cancel_all(self):
        for url, worker in list(self._active_workers.items()):
            worker.cancel()
            worker.wait(2000)
        self._active_workers.clear()
        self._queue.clear()
        self.queue_changed.emit()

    def active_count(self) -> int:
        return len(self._active_workers)

    def queue_count(self) -> int:
        return len(self._queue)

    def completed_count(self) -> int:
        return len(self._completed)

    def _start_download(self, video_info: dict):
        url = video_info.get("url", "")
        filename = video_info.get("filename", "video.mp4")

        worker = DownloadWorker(video_info, self.download_dir)
        worker._ffmpeg_path = self._ffmpeg_path
        worker._nm3u8dl_path = self._nm3u8dl_path
        cookies_file = self._ensure_cookies()
        if cookies_file and os.path.isfile(cookies_file):
            worker._cookies_file = cookies_file
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)

        self._active_workers[url] = worker
        self.download_started.emit(url, filename)
        self.queue_changed.emit()
        worker.start()

    def _on_progress(self, url: str, pct: float, speed: float, dl_mb: float, total_mb: float):
        self.download_progress.emit(url, pct, speed, dl_mb, total_mb)

    def _on_finished(self, url: str, filepath: str):
        worker = self._active_workers.pop(url, None)
        if worker:
            worker.quit()
            worker.wait(1000)
        self._completed.append({"url": url, "filepath": filepath})
        self.download_finished.emit(url, filepath)
        self.queue_changed.emit()
        self._process_queue()

    def _on_error(self, url: str, error: str):
        worker = self._active_workers.pop(url, None)
        if worker:
            worker.quit()
            worker.wait(1000)
        self.download_error.emit(url, error)
        self.queue_changed.emit()
        self._process_queue()

    def _process_queue(self):
        while self._queue and len(self._active_workers) < self._max_concurrent:
            video_info = self._queue.pop(0)
            self._start_download(video_info)
