"""VideoSnatch Network Interceptor - 网络请求拦截与视频检测"""

import re
import json
import logging
from urllib.parse import urlparse, parse_qs
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".webm", ".m3u8", ".ts", ".flv", ".avi", ".mkv", ".mov",
    ".wmv", ".m4v", ".3gp", ".ogv", ".mpg", ".mpeg", ".qt", ".f4v",
}

VIDEO_MIME_PREFIXES = {
    "video/", "application/vnd.apple.mpegurl", "application/x-mpegurl",
    "application/dash+xml",
}

STREAM_KEYWORDS = [
    "video", "media", "stream", "playlist", "manifest", "chunk",
    "fragment", "segment", "dash", "hls", "m3u",
]

BLOCKED_KEYWORDS = [
    "analytics", "tracking", "pixel", "advertisement", "adservice",
    "doubleclick", "googleads", "facebook.com/tr", "metrics",
    "avatar", "thumbnail", "sprite", "icon", "emoji",
    "banner", "logo", "badge", "spinner", "loading",
]


class CaptchaInfo:
    """检测到的验证码信息"""

    def __init__(self, captcha_type: str, page_url: str = "",
                 detect_method: str = "script", site_key: str = ""):
        self.captcha_type = captcha_type       # recaptcha_v2, recaptcha_v3, turnstile, custom
        self.page_url = page_url
        self.detect_method = detect_method     # script, network, dom
        self.site_key = site_key
        self.resolved = False
        self.resolve_time = 0.0

    def to_dict(self) -> dict:
        return {
            "captcha_type": self.captcha_type,
            "page_url": self.page_url,
            "detect_method": self.detect_method,
            "site_key": self.site_key,
            "resolved": self.resolved,
        }

    def __repr__(self):
        return f"CaptchaInfo(type={self.captcha_type}, resolved={self.resolved})"

NON_VIDEO_EXTENSIONS = {
    ".js", ".css", ".json", ".xml", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".svg", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".wasm", ".pdf", ".txt", ".csv", ".map",
}


class VideoInfo:
    """检测到的视频信息"""

    def __init__(self, url: str, mime: str = "", size: int = 0,
                 page_url: str = "", filename: str = "",
                 format_type: str = "direct", headers=None, duration: float = 0,
                 quality: str = "Best"):
        self.url = url
        self.mime = mime
        self.size = size
        self.page_url = page_url
        self.format_type = format_type  # direct, hls, dash, embedded, ws_stream
        self.headers = headers if headers else {}
        self.filename = filename or self._guess_filename()
        self.duration = duration

        self.quality = quality
    def _guess_filename(self) -> str:
        parsed = urlparse(self.url)
        path = parsed.path
        if not path or path == "/":
            return f"video_{hash(self.url) & 0xFFFFFFFF}.mp4"
        name = path.rstrip("/").split("/")[-1]
        if not name or "." not in name:
            name = f"video_{hash(self.url) & 0xFFFFFFFF}.mp4"
        return name

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "mime": self.mime,
            "size": self.size,
            "page_url": self.page_url,
            "format": self.format_type,
            "filename": self.filename,
            "headers": self.headers,
            "duration": self.duration,
            "quality": self.quality,
        }

    def __eq__(self, other):
        if isinstance(other, VideoInfo):
            return self.url == other.url
        return False

    def __hash__(self):
        return hash(self.url)


# 注入到每个页面中的检测脚本 + 浮动下载按钮 UI
# 检测 m3u8/video 元素，并在页面右下角显示一个浮动按钮
DETECTOR_SCRIPT = r"""
(() => {
    const PREFIX = '__VSNATCH__:';
    const seen = new Set();
    const detectedVideos = [];
    let uiCreated = false;
    let btn, badge, panel, listEl;
    let panelVisible = false;

    // 全局错误捕获，调试用
    window.__vs_debug = function(msg) {
        try { console.log('__VSNATCH_DEBUG__:' + String(msg).slice(0,200)); } catch(e) {}
    };
    try {

    // ── 工具函数 ──────────────────────────────

    // 递归查找所有元素（穿透 Shadow DOM）
    // 使用 TreeWalker 比 querySelectorAll('*') 性能更好
    function queryAllDeep(selector, root) {
        root = root || document;
        let results = [];
        try {
            // 当前 root 中的匹配
            results = results.concat(Array.from(root.querySelectorAll(selector)));
            // 用 TreeWalker 遍历所有节点找 shadowRoot，比 querySelectorAll('*') 快得多
            const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
            while (walker.nextNode()) {
                const el = walker.currentNode;
                if (el.shadowRoot) {
                    results = results.concat(queryAllDeep(selector, el.shadowRoot));
                }
            }
        } catch(e) {}
        return results;
    }

    function formatDuration(secs) {
        if (!secs || isNaN(secs)) return '';
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        const s = Math.floor(secs % 60);
        if (h > 0) return h + ':' + String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
        return m + ':' + String(s).padStart(2,'0');
    }

    function guessFilename(url, index) {
        try {
            const u = new URL(url);
            const name = u.pathname.split('/').filter(s => s).pop();
            if (name && name.includes('.')) return decodeURIComponent(name);
        } catch(e) {}
        return 'video_' + (index + 1) + '.mp4';
    }

    function truncate(str, max) {
        return str.length > max ? str.slice(0, max) + '...' : str;
    }

    // ── 下载请求（暴露到全局供 inline onclick 调用） ──

    // 通过索引查找视频（避免 JSON 嵌入 HTML 属性导致引号冲突）
    window.__vs_download_by_idx = function(idx) {
        const video = detectedVideos[idx];
        if (!video) return;
        video.quality = video.selectedQuality || 'Best';
        window.__vs_download(video);
    };

    window.__vs_download = function(video) {
        const data = JSON.stringify({
            type: 'download',
            url: video.url,
            pageUrl: video.pageUrl || location.href,
            filename: video.filename || '',
            format: video.format || 'direct',
            duration: video.duration || 0,
            quality: video.quality || 'Best',
        });
        console.log('__VSNATCH_DOWNLOAD__:' + data);
    };

    // ── 浮动按钮 UI ──────────────────────────

    function createUI() {
        if (uiCreated) return;
        if (!document.body) { setTimeout(createUI, 200); return; }

        // === 浮动按钮 (右下角圆点) ===
        btn = document.createElement('div');
        btn.id = '__vs_btn';
        Object.assign(btn.style, {
            position: 'fixed', top: '24px', left: '24px',
            zIndex: '2147483647', width: '52px', height: '52px',
            borderRadius: '50%', background: '#2563eb',
            boxShadow: '0 4px 16px rgba(37,99,235,0.5)',
            cursor: 'pointer', display: 'none',
            alignItems: 'center', justifyContent: 'center',
            transition: 'transform 0.2s, box-shadow 0.2s',
            fontFamily: 'Segoe UI, system-ui, sans-serif', userSelect: 'none',
        });
        btn.onmouseenter = () => { btn.style.transform = 'scale(1.12)'; btn.style.boxShadow = '0 6px 20px rgba(37,99,235,0.6)'; };
        btn.onmouseleave = () => { btn.style.transform = 'scale(1)'; btn.style.boxShadow = '0 4px 16px rgba(37,99,235,0.5)'; };

        // 下载图标 (SVG) — 用 DOM 方法创建，避免 Trusted Types 阻止 innerHTML
        const svgNS = 'http://www.w3.org/2000/svg';
        const icon = document.createElementNS(svgNS, 'svg');
        icon.setAttribute('viewBox', '0 0 24 24');
        icon.setAttribute('width', '26');
        icon.setAttribute('height', '26');
        icon.style.fill = 'white';
        const svgPath = document.createElementNS(svgNS, 'path');
        svgPath.setAttribute('d', 'M5 20h14v-2H5v2zm7-18L5.33 9h3.84v4h3.66V9h3.84L12 2z');
        icon.appendChild(svgPath);
        btn.appendChild(icon);

        // 视频数量角标
        badge = document.createElement('div');
        Object.assign(badge.style, {
            position: 'absolute', top: '-4px', right: '-4px',
            background: '#dc2626', color: 'white',
            fontSize: '11px', minWidth: '20px', height: '20px',
            borderRadius: '10px', display: 'none',
            alignItems: 'center', justifyContent: 'center',
            fontWeight: 'bold', fontFamily: 'Segoe UI, sans-serif',
            boxShadow: '0 2px 6px rgba(220,38,38,0.4)',
        });
        btn.appendChild(badge);

        // === 弹层面板 ===
        panel = document.createElement('div');
        panel.id = '__vs_panel';
        Object.assign(panel.style, {
            position: 'fixed', top: '88px', left: '24px',
            zIndex: '2147483646', width: '380px', maxHeight: '420px',
            background: 'white', borderRadius: '14px',
            boxShadow: '0 8px 40px rgba(0,0,0,0.25)',
            display: 'none', overflow: 'hidden',
            fontFamily: 'Segoe UI, system-ui, sans-serif',
            fontSize: '13px', color: '#1a1a2e',
            animation: 'none',
        });

        // 面板标题栏
        const header = document.createElement('div');
        Object.assign(header.style, {
            padding: '14px 18px', background: '#2563eb',
            color: 'white', fontWeight: 'bold', fontSize: '15px',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        });
        // 不用 innerHTML（YouTube 的 Trusted Types CSP 会阻止）
        const titleSpan = document.createElement('span');
        titleSpan.textContent = '\u25BC VideoSnatch';
        header.appendChild(titleSpan);
        const closeBtn = document.createElement('span');
        closeBtn.textContent = '\u00D7';
        closeBtn.style.cssText = 'cursor:pointer;font-size:20px;line-height:1;opacity:0.8;';
        closeBtn.onmouseenter = () => closeBtn.style.opacity = '1';
        closeBtn.onmouseleave = () => closeBtn.style.opacity = '0.8';
        closeBtn.onclick = (e) => { e.stopPropagation(); togglePanel(); };
        header.appendChild(closeBtn);
        panel.appendChild(header);

        // 视频列表容器
        listEl = document.createElement('div');
        Object.assign(listEl.style, {
            overflowY: 'auto', maxHeight: '360px',
        });
        panel.appendChild(listEl);

        // 按钮点击：展开/收起面板
        btn.onclick = togglePanel;

        // 点击外部关闭面板
        document.addEventListener('click', (e) => {
            if (panelVisible && !panel.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
                togglePanel();
            }
        });

        document.body.appendChild(btn);
        document.body.appendChild(panel);
        uiCreated = true;
    }

    function togglePanel() {
        panelVisible = !panelVisible;
        panel.style.display = panelVisible ? 'block' : 'none';
        if (panelVisible) renderList();
    }

    function renderList() {
        if (!listEl) return;
        // 清空列表（用 DOM 方法避免 Trusted Types 阻止）
        while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
        
        if (detectedVideos.length === 0) {
            const emptyMsg = document.createElement('div');
            Object.assign(emptyMsg.style, {
                padding: '30px', textAlign: 'center',
                color: '#9ca3af', fontSize: '14px',
            });
            emptyMsg.textContent = '\u26F5 No videos detected';
            listEl.appendChild(emptyMsg);
            return;
        }
        
        for (let i = 0; i < detectedVideos.length; i++) {
            const v = detectedVideos[i];
            const dur = v.duration ? formatDuration(v.duration) : '';
            const fmtMap = { hls: 'HLS', dash: 'DASH', direct: 'MP4', ws_stream: 'WS' };
            const tag = fmtMap[v.format] || v.format.toUpperCase();
            const name = truncate(v.filename || guessFilename(v.url, i), 45);
            
            // 行容器
            const row = document.createElement('div');
            Object.assign(row.style, {
                padding: '11px 16px', borderBottom: '1px solid #f0f0f0',
                cursor: 'default', transition: 'background 0.15s',
            });
            row.onmouseenter = function() { this.style.background = '#f3f4f6'; };
            row.onmouseleave = function() { this.style.background = 'transparent'; };
            
            // 水平布局
            const hbox = document.createElement('div');
            Object.assign(hbox.style, {
                display: 'flex', alignItems: 'center',
                justifyContent: 'space-between',
            });
            
            // 左侧信息
            const infoDiv = document.createElement('div');
            Object.assign(infoDiv.style, { flex: '1', minWidth: '0' });
            
            const nameDiv = document.createElement('div');
            Object.assign(nameDiv.style, {
                fontSize: '13px', fontWeight: '500', color: '#1a1a2e',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            });
            nameDiv.textContent = name;
            infoDiv.appendChild(nameDiv);
            
            const metaDiv = document.createElement('div');
            Object.assign(metaDiv.style, {
                fontSize: '11px', color: '#6b7280', marginTop: '3px',
            });
            
            const tagSpan = document.createElement('span');
            Object.assign(tagSpan.style, {
                background: '#e5e7eb', padding: '1px 6px',
                borderRadius: '3px', fontWeight: '600',
            });
            tagSpan.textContent = tag;
            metaDiv.appendChild(tagSpan);
            
            if (dur) {
                const durSpan = document.createElement('span');
                durSpan.style.marginLeft = '8px';
                durSpan.textContent = '\u23F1 ' + dur;
                metaDiv.appendChild(durSpan);
            }
            infoDiv.appendChild(metaDiv);
            hbox.appendChild(infoDiv);
            
            // DL 按钮
            // == quality selector ==
            const qSelect = document.createElement("select");
            Object.assign(qSelect.style, {
                marginLeft: '6px', padding: '3px 2px', fontSize: '11px',
                border: '1px solid #d1d5db', borderRadius: '4px',
                background: 'white', color: '#1a1a2e', cursor: 'pointer',
                outline: 'none',
            });
            ['Best','2160p','1080p','720p','480p','360p'].forEach(function(q) {
                const opt = document.createElement("option");
                opt.value = q;
                opt.textContent = q;
                if (q === (v.selectedQuality || "Best")) opt.selected = true;
                qSelect.appendChild(opt);
            });
            qSelect.onchange = function() {
                detectedVideos[i].selectedQuality = this.value;
            };
            hbox.appendChild(qSelect);
            
            const dlBtn = document.createElement('button');
            dlBtn.setAttribute('data-vs', String(i));
            Object.assign(dlBtn.style, {
                marginLeft: '6px', padding: '5px 14px',
                background: '#16a34a', color: 'white', border: 'none',
                borderRadius: '6px', fontSize: '12px', fontWeight: '600',
                cursor: 'pointer', whiteSpace: 'nowrap',
                transition: 'background 0.15s',
            });
            dlBtn.onmouseenter = function() { this.style.background = '#22c55e'; };
            dlBtn.onmouseleave = function() { this.style.background = '#16a34a'; };
            dlBtn.onclick = function(e) {
                e.stopPropagation();
                window.__vs_download_by_idx(parseInt(this.getAttribute('data-vs'), 10));
            };
            dlBtn.textContent = '\u2B07 DL';
            hbox.appendChild(dlBtn);
            
            row.appendChild(hbox);
            listEl.appendChild(row);
        }
    }

    // 已不再需要 escapeHtml — 全部使用 DOM 方法
    function escapeHtml(s) {
        return String(s).replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function updateUI() {
        if (!uiCreated) return;
        const count = detectedVideos.length;
        if (count > 0) {
            btn.style.display = 'flex';
            badge.style.display = 'flex';
            badge.textContent = count > 99 ? '99+' : String(count);
        } else {
            btn.style.display = 'none';
        }
        if (panelVisible) renderList();
    }

    // ── 视频检测报告 ──────────────────────────

    function report(type, data) {
        const key = data.url || JSON.stringify(data);
        if (seen.has(key)) return;
        seen.add(key);
        console.log(PREFIX + JSON.stringify(Object.assign({type}, data)));

        // 同步更新浮动按钮 UI
        if (type === 'video' || type === 'm3u8') {
            const fmt = type === 'm3u8' ? 'hls' : 'direct';
            const dur = data.duration || 0;
            const filename = guessFilename(data.url, detectedVideos.length);
            detectedVideos.push({
                url: data.url,
                pageUrl: data.pageUrl || location.href,
                duration: dur,
                format: fmt,
                filename: filename,
            });
            updateUI();
        }
    }

    // ── 检测逻辑（原有） ───────────────────────

    function detectHlsJs() {
        try {
            if (window.Hls && window.Hls.isSupported()) {
                queryAllDeep('video').forEach(v => {
                    if (v._hls && v._hls.url) report('m3u8', {url: v._hls.url, pageUrl: location.href});
                });
            }
            Object.keys(window).forEach(k => {
                const v = window[k];
                if (v && v.url && typeof v.url === 'string' && v.url.includes('.m3u8')) {
                    report('m3u8', {url: v.url, pageUrl: location.href});
                }
            });
        } catch(e) {}
    }

    function detectVideoAttrs() {
        queryAllDeep('video').forEach(v => {
            ['data-src','data-url','data-config','data-setup','data-resource'].forEach(attr => {
                const val = v.getAttribute(attr);
                if (val && val.includes('.m3u8')) report('m3u8', {url: val, pageUrl: location.href});
            });
        });
    }

    function detectCaptcha() {
        try {
            const rc2 = document.querySelector('.g-recaptcha, div[class*="g-recaptcha"], ' +
                'iframe[src*="recaptcha/"], div[data-sitekey]');
            if (rc2) {
                const key = rc2.getAttribute('data-sitekey') || '';
                report('captcha', {captchaType: 'recaptcha_v2', pageUrl: location.href, siteKey: key});
            }
            const rc3 = document.querySelector('.grecaptcha-badge, iframe[src*="recaptcha/api.js"]');
            if (rc3) {
                report('captcha', {captchaType: 'recaptcha_v3', pageUrl: location.href, siteKey: ''});
            }
            const ts = document.querySelector('.cf-turnstile, div[class*="turnstile"], ' +
                'iframe[src*="turnstile"], div[data-cf-turnstile]');
            if (ts) {
                const key = ts.getAttribute('data-sitekey') || '';
                report('captcha', {captchaType: 'turnstile', pageUrl: location.href, siteKey: key});
            }
            if (document.querySelector('iframe[src*="funcaptcha"], div[class*="fun-captcha"]')) {
                report('captcha', {captchaType: 'funcaptcha', pageUrl: location.href, siteKey: ''});
            }
            if (document.querySelector('div[class*="h-captcha"], iframe[src*="hcaptcha"]')) {
                report('captcha', {captchaType: 'hcaptcha', pageUrl: location.href, siteKey: ''});
            }
            document.querySelectorAll('iframe').forEach(f => {
                const src = (f.src || '').toLowerCase();
                if (src.includes('captcha') || src.includes('verify')) {
                    report('captcha', {captchaType: 'generic', pageUrl: location.href, siteKey: src.slice(0,120)});
                }
            });
        } catch(e) {}
    }

    // ── 初始化 ────────────────────────────────

    // 先创建 UI
    createUI();

    // 心跳：每2秒检查按钮是否存在（处理 SPA 框架删除外来 DOM 元素的情况）
    setInterval(function() {
        if (!document.getElementById('__vs_btn')) {
            uiCreated = false;
            btn = null; panel = null; badge = null; listEl = null;
            panelVisible = false;
            createUI();
            updateUI();
        }
    }, 2000);

    // 定期检测视频 + 验证码
    setInterval(() => {
        // 穿透 Shadow DOM 查找所有 <video> 元素（YouTube 等 SPA 网站）
        queryAllDeep('video').forEach(v => {
            // 条件1：有 http src 且视频已加载
            if (v.duration && v.src && v.src.startsWith('http')) {
                report('video', {url: v.src, pageUrl: location.href, duration: v.duration});
            }
            // 条件2：blob URL（YouTube 等使用 MediaSource）— 只要有 videoWidth 说明正在播放
            else if (v.videoWidth > 0 && v.duration && (!v.src || v.src.startsWith('blob:'))) {
                report('video', {url: location.href, pageUrl: location.href, duration: v.duration});
            }
            // <source> 子元素
            try {
                v.querySelectorAll('source').forEach(s => {
                    if (s.src && s.src.startsWith('http')) {
                        report('video', {url: s.src, pageUrl: location.href, type: s.type || ''});
                    }
                });
            } catch(e) {}
        });
        queryAllDeep('[src*=".m3u8"], [href*=".m3u8"], [data-src*=".m3u8"]').forEach(el => {
            const u = el.src || el.href || el.getAttribute('data-src');
            if (u && u.startsWith('http')) report('m3u8', {url: u, pageUrl: location.href});
        });
        detectHlsJs();
        detectVideoAttrs();
        detectCaptcha();
    }, 1500);

    // 拦截 fetch Response 中的 m3u8 内容
    const _origText = Response.prototype.text;
    Response.prototype.text = function() {
        return _origText.call(this).then(text => {
            try {
                if (text.trim().startsWith('#EXTM3U')) {
                    const url = this.url || '';
                    if (url && url.startsWith('http')) report('m3u8', {url, pageUrl: location.href});
                }
            } catch(e) {}
            return text;
        });
    };

    // 拦截 XHR 中的 m3u8 内容
    const _origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url) {
        this.addEventListener('load', () => {
            try {
                const text = this.responseText;
                if (text && text.trim().startsWith('#EXTM3U')) {
                    const reqUrl = (typeof url === 'string') ? url : (this.responseURL || '');
                    if (reqUrl && reqUrl.startsWith('http')) report('m3u8', {url: reqUrl, pageUrl: location.href});
                }
            } catch(e) {}
        });
        return _origOpen.apply(this, arguments);
    };

    } catch (__vs_err) {
        // 将初始化错误打印到 console，让引擎可以捕获
        try { console.log('__VSNATCH_DEBUG__:INIT_ERROR:' + String(__vs_err.message || __vs_err).slice(0,200)); } catch(e) {}
    }
})();
"""


class PageScanner:
    """扫描页面 HTML 查找视频元素"""

    @staticmethod
    async def scan_page(page) -> list[VideoInfo]:
        """扫描页面中的 video 标签和视频链接"""
        videos = []
        try:
            script = """
            () => {
                const results = [];
                // 查找 <video> 标签
                document.querySelectorAll('video').forEach(v => {
                    if (v.src) results.push({url: v.src, type: 'direct'});
                    v.querySelectorAll('source').forEach(s => {
                        if (s.src) results.push({url: s.src, type: 'direct', mime: s.type || ''});
                    });
                });
                // 查找 HLS/DASH 链接
                document.querySelectorAll('[src*=".m3u8"], [data-src*=".m3u8"], ' +
                    '[href*=".m3u8"], [src*=".mpd"], [data-src*=".mpd"]').forEach(el => {
                    results.push({url: el.src || el.href || el.getAttribute('data-src'), type: 'hls'});
                });
                // 查找 iframe 中的视频
                document.querySelectorAll('iframe[src*="youtube"], iframe[src*="bilibili"], ' +
                    'iframe[src*="player"]').forEach(el => {
                    results.push({url: el.src, type: 'embedded'});
                });
                return results;
            }
            """
            result = await page.evaluate(script)
            for item in result:
                url = item.get("url", "")
                if url and PageScanner._is_valid_video_url(url):
                    vtype = item.get("type", "direct")
                    mime = item.get("mime", "")
                    vi = VideoInfo(
                        url=url, mime=mime, page_url=page.url,
                        format_type=vtype if vtype in ("hls", "embedded") else "direct",
                    )
                    videos.append(vi)
        except Exception as e:
            logger.debug(f"页面扫描失败: {e}")
        return videos

    @staticmethod
    def _is_valid_video_url(url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        for kw in BLOCKED_KEYWORDS:
            if kw in url.lower():
                return False
        return True


class VideoInterceptor(QObject):
    """网络请求拦截器 - 检测视频请求、验证码、WebSocket 流"""

    video_detected = pyqtSignal(object)      # VideoInfo
    page_videos_updated = pyqtSignal(list)   # list[VideoInfo]
    scan_started = pyqtSignal()
    scan_finished = pyqtSignal(int)
    captcha_detected = pyqtSignal(object)    # CaptchaInfo
    captcha_resolved = pyqtSignal(object)    # CaptchaInfo
    download_requested = pyqtSignal(object)  # VideoInfo - from floating button click

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cdp_session = None
        self._page = None
        self._detected_videos: list[VideoInfo] = []
        self._seen_urls: set[str] = set()
        self._active = False
        self._captchas: list[CaptchaInfo] = []
        self._ws_monitoring = False

    def attach(self, cdp_session, page):
        """绑定到 CDP 会话和页面"""
        self._cdp_session = cdp_session
        self._page = page
        self._detected_videos = []
        self._seen_urls = set()
        self._captchas = []

    async def start_intercepting(self):
        """开始拦截网络请求并注入检测脚本"""
        if not self._cdp_session:
            logger.warning("CDP 会话未就绪，无法拦截")
            return

        self._active = True

        try:
            await self._cdp_session.send("Network.enable")
            self._cdp_session.on(
                "Network.responseReceived",
                self._on_response_received
            )
            self._cdp_session.on(
                "Network.requestWillBeSent",
                self._on_request_will_be_sent
            )

            # 注入前端检测脚本到每个新页面
            await self._cdp_session.send(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": DETECTOR_SCRIPT}
            )

            # 额外：在当前已打开的页面上立即执行一次脚本
            try:
                await self._page.evaluate(DETECTOR_SCRIPT)
            except Exception as eval_err:
                logger.debug(f"当前页面直接注入脚本失败（导航后会自动生效）: {eval_err}")

            # 监听 console 消息（来自注入脚本的检测报告）
            await self._cdp_session.send("Console.enable")
            self._cdp_session.on(
                "Console.messageAdded",
                self._on_console_message
            )

            # WebSocket 监控（可发现通过 WS 传输的视频流元数据）
            await self._enable_websocket_monitoring()

            logger.info("网络拦截+前端检测+WS监控已启动")
        except Exception as e:
            logger.error(f"启动拦截检测失败: {e}")

    async def stop_intercepting(self):
        """停止网络拦截"""
        self._active = False
        self._ws_monitoring = False
        try:
            if self._cdp_session:
                await self._cdp_session.send("Network.disable")
                await self._cdp_session.send("Console.disable")
        except Exception:
            pass

    async def _enable_websocket_monitoring(self):
        """启用 WebSocket 和 Beacon 流量监控"""
        if not self._cdp_session:
            return
        try:
            self._cdp_session.on(
                "Network.webSocketCreated",
                self._on_ws_created
            )
            self._cdp_session.on(
                "Network.webSocketFrameReceived",
                self._on_ws_frame
            )
            self._cdp_session.on(
                "Network.webSocketFrameError",
                self._on_ws_error
            )
            self._ws_monitoring = True
            logger.debug("WebSocket 监控已启用")
        except Exception as e:
            logger.debug(f"WebSocket 监控启用失败: {e}")

    def _on_ws_created(self, params: dict):
        """WebSocket 连接建立时触发"""
        if not self._active:
            return
        try:
            url = params.get("url", "")
            if not url:
                return
            url_lower = url.lower()
            # 检测视频流相关的 WebSocket 连接
            video_keywords = ["video", "stream", "media", "m3u8", ".ts", "live", "play"]
            if any(kw in url_lower for kw in video_keywords):
                logger.info(f"检测到视频相关 WebSocket: {url[:100]}")
                vi = VideoInfo(
                    url=url,
                    page_url=self._get_page_url(),
                    format_type="ws_stream",
                )
                self._add_external(vi)
        except Exception as e:
            logger.debug(f"WS created 处理异常: {e}")

    def _on_ws_frame(self, params: dict):
        """WebSocket 帧到达时触发——检测帧内容中的视频元数据"""
        if not self._active:
            return
        try:
            response = params.get("response", {})
            payload = response.get("payloadData", "")
            if not payload or len(payload) > 4096:
                return
            # 检测 m3u8 或 mpd URL 出现在 WS 帧中
            if '.m3u8' in payload or '.mpd' in payload:
                import re as _re
                urls = _re.findall(r'https?://[^\s"\'<>]+?(?:\.m3u8|\.mpd)[^\s"\'<>]*', payload)
                for u in urls:
                    if u not in self._seen_urls:
                        logger.info(f"WebSocket 帧中检测到视频流: {u[:80]}")
                        vi = VideoInfo(
                            url=u, page_url=self._get_page_url(),
                            format_type="hls" if ".m3u8" in u else "dash",
                        )
                        self._add_external(vi)
        except Exception as e:
            logger.debug(f"WS frame 处理异常: {e}")

    def _on_ws_error(self, params: dict):
        """WebSocket 错误时记录"""
        if not self._active:
            return
        try:
            url = params.get("url", "")
            error = params.get("errorMessage", "")
            if url and error:
                logger.debug(f"WebSocket 错误 [{url[:60]}]: {error[:100]}")
        except Exception:
            pass

    def clear_detected(self):
        """清空检测列表"""
        self._detected_videos = []
        self._seen_urls = set()
        self._captchas = []

    def get_detected_videos(self) -> list[VideoInfo]:
        return list(self._detected_videos)

    def remove_video(self, video_url: str):
        self._detected_videos = [
            v for v in self._detected_videos if v.url != video_url
        ]
        self._seen_urls.discard(video_url)
        self.page_videos_updated.emit(self.get_detected_videos())

    def _on_response_received(self, params: dict):
        """处理 CDP responseReceived 事件"""
        if not self._active:
            return

        try:
            response = params.get("response", {})
            url = response.get("url", "")
            mime = response.get("mimeType", "")
            headers = response.get("headers", {})
            content_length = int(headers.get("Content-Length", 0)) or 0
            status = response.get("status", 0)

            if not url or status >= 400:
                return

            if self._is_video_response(url, mime, content_length):
                self._add_video(url, mime, content_length, "response")
        except Exception as e:
            logger.debug(f"处理 response 事件异常: {e}")

    def _on_request_will_be_sent(self, params: dict):
        """处理 CDP requestWillBeSent 事件"""
        if not self._active:
            return

        try:
            request = params.get("request", {})
            url = request.get("url", "")

            if not url:
                return

            if self._is_video_request(url):
                self._add_video(url, "", 0, "request")
        except Exception:
            pass

    def process_console_text(self, text: str):
        """处理来自页面 console 的视频检测/下载请求文本（Playwright 原生 console 事件）"""
        # 注意：不检查 self._active，因为 Playwright 原生 console 事件
        # 即使 CDP 会话失败也能正常工作（由 engine.py 直接转发）
        try:
            # 处理页面浮动按钮发起的下载请求
            if text.startswith("__VSNATCH_DOWNLOAD__:"):
                payload = json.loads(text[len("__VSNATCH_DOWNLOAD__:"):])
                vi = VideoInfo(
                    url=payload.get("url", ""),
                    page_url=payload.get("pageUrl", ""),
                    filename=payload.get("filename", ""),
                    format_type=payload.get("format", "direct"),
                    quality=payload.get("quality", "Best"),
                    duration=payload.get("duration", 0),
                )
                logger.info(f"浮动按钮请求下载: {vi.filename}")
                self.download_requested.emit(vi)
                return

            if not text.startswith("__VSNATCH__:"):
                return
            payload = json.loads(text[len("__VSNATCH__:"):])
            vtype = payload.get("type", "")
            url = payload.get("url", "")
            page_url = payload.get("pageUrl", "")
            duration = payload.get("duration", 0)
            if vtype == "captcha":
                ct = payload.get("captchaType", "generic")
                sk = payload.get("siteKey", "")
                ci = CaptchaInfo(
                    captcha_type=ct,
                    page_url=page_url or self._get_page_url(),
                    detect_method="script",
                    site_key=sk,
                )
                self._on_captcha_detected(ci)
                return
            if not url:
                return
            if vtype == "m3u8":
                vi = VideoInfo(url=url, page_url=page_url, format_type="hls", duration=duration)
                self._add_external(vi)
            elif vtype == "video":
                vi = VideoInfo(url=url, page_url=page_url, format_type="direct", duration=duration)
                self._add_external(vi)
        except Exception as e:
            logger.debug(f"解析前端检测消息失败: {e}")

    def _on_console_message(self, params: dict):
        """处理 CDP Console.messageAdded 事件（兼容旧方式）"""
        try:
            msg = params.get("message", {})
            text = msg.get("text", "")
            self.process_console_text(text)
        except Exception:
            pass

    def _on_captcha_detected(self, ci: CaptchaInfo):
        """处理 CAPTCHA 检测结果"""
        # 对同页面同类型去重
        for existing in self._captchas:
            if existing.captcha_type == ci.captcha_type and existing.page_url == ci.page_url:
                return
        self._captchas.append(ci)
        logger.warning(f"检测到验证码: {ci.captcha_type} @ {ci.page_url[:60]}")
        self.captcha_detected.emit(ci)

    def get_detected_captchas(self) -> list[CaptchaInfo]:
        return list(self._captchas)

    def mark_captcha_resolved(self, ci: CaptchaInfo):
        """标记验证码为已解决"""
        for c in self._captchas:
            if c is ci or (c.captcha_type == ci.captcha_type and c.page_url == ci.page_url):
                c.resolved = True
                import time
                c.resolve_time = time.time()
                self.captcha_resolved.emit(c)
                break

    def _add_external(self, vi: VideoInfo):
        """添加来自注入脚本检测到的视频（跳过 URL 去重）"""
        if vi.url in self._seen_urls:
            return
        self._seen_urls.add(vi.url)
        self._detected_videos.append(vi)
        self.video_detected.emit(vi)
        self.page_videos_updated.emit(self.get_detected_videos())
        logger.info(f"前端检测到视频: {vi.filename} ({vi.format_type})")

    def _is_video_response(self, url: str, mime: str, size: int) -> bool:
        if size > 0 and size < 102400:
            return False
        ext = self._get_extension(url)
        if ext in NON_VIDEO_EXTENSIONS:
            return False
        for prefix in VIDEO_MIME_PREFIXES:
            if mime.startswith(prefix):
                return True
        return ext in VIDEO_EXTENSIONS

    def _is_video_request(self, url: str) -> bool:
        parsed = urlparse(url)
        ext = self._get_extension(url)

        # 有视频扩展名 → 通过
        if ext in VIDEO_EXTENSIONS:
            return True

        # 有明确的非视频扩展名 → 立即拒绝
        if ext in NON_VIDEO_EXTENSIONS:
            return False

        # 无扩展名或未知扩展名：用关键字匹配路径
        path_lower = parsed.path.lower()
        for kw in BLOCKED_KEYWORDS:
            if kw in url.lower():
                return False
        for kw in STREAM_KEYWORDS:
            if kw in path_lower:
                return True
        return False

    def _add_video(self, url: str, mime: str, size: int,
                   source: str = "response"):
        if url in self._seen_urls:
            return
        for kw in BLOCKED_KEYWORDS:
            if kw in url.lower():
                return

        ext = self._get_extension(url)
        page_url = self._get_page_url() or ""

        fmt = "hls" if ".m3u8" in url else "dash" if ".mpd" in url else "direct"

        vi = VideoInfo(
            url=url, mime=mime, size=size,
            page_url=page_url, format_type=fmt,
        )

        self._seen_urls.add(url)
        self._detected_videos.append(vi)
        self.video_detected.emit(vi)
        self.page_videos_updated.emit(self.get_detected_videos())
        logger.info(f"检测到视频: {vi.filename} ({fmt}, {size} bytes)")

    def _get_page_url(self) -> str:
        try:
            if self._page:
                return self._page.url
        except Exception:
            pass
        return ""

    @staticmethod
    def _get_extension(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        if "." in path:
            ext = "." + path.split(".")[-1].split("?")[0].lower()
            return ext
        return ""

    @staticmethod
    def _get_filename_from_url(url: str) -> str:
        path = urlparse(url).path
        return path.split("/")[-1].split("?")[0] or "video.mp4"


