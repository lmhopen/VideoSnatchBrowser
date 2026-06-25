---
name: videosnatch-crawler
description: >
  Trigger when the user requests web crawling, scraping, data extraction, page 
  screenshot, batch URL collection, or any task requiring automated browsing. 
  Uses CloakBrowser + Playwright with stealth anti-detection and persistent 
  session profile. Built on top of the VideoSnatch browser engine — shares the 
  same browser_profile so cookies and login state carry over.
  Triggers: "crawl", "scrape", "extract data", "爬取", "抓取数据", "批量爬取", 
  "截图", "网页数据提取", "从网站提取", "collect data from", "gather links", 
  "scrape this page".
---

# VideoSnatch Crawler Skill

基于 CloakBrowser + Playwright 的智能爬虫引擎。与 VideoSnatch 浏览器共享同一个 `browser_profile/` 目录，登录态互通。

## 能力

| 功能 | 说明 |
|------|------|
| 页面导航 | URL 导航，自动补全协议，支持各种 wait_until 策略 |
| 文本提取 | 提取页面可见文本或指定元素的文本 |
| 结构化提取 | 按 CSS 选择器规则批量提取字段 |
| 链接/图片提取 | 提取页面所有链接和图片 |
| 表格提取 | 提取 HTML 表格为结构化数据 |
| 元数据提取 | title, description, og:image, keywords 等 |
| 截图 | 全页或可视区域截图 |
| JS 执行 | 在页面上下文中运行任意 JavaScript |
| 页面交互 | 点击、填表、下拉选择、滚动加载 |
| Cookie 管理 | 获取、设置、清除 Cookie |
| 批量爬取 | 多 URL 列表批量采集 |
| 会话保持 | 复用 browser_profile，Cookie 和登录态与 GUI 浏览器互通 |

## 用法

### Python API（推荐给 AI 使用）

```python
from videosnatch.crawler import Crawler
import asyncio


# 方式一：上下文管理器（自动管理生命周期）
async with Crawler() as crawler:
    await crawler.start(headless=True)
    
    # 导航
    url = await crawler.navigate("https://example.com")
    
    # 提取文本
    text = await crawler.extract_text()
    
    # 提取结构化数据
    data = await crawler.extract_structured({
        "title": "h1",
        "description": "meta[name=description]::attr(content)",
        "price": ".price",
        "image": "img.main-img::attr(src)",
    })
    
    # 提取所有链接
    links = await crawler.extract_links()
    
    # 智能提取（自动判断页面类型）
    result = await crawler.extract(mode="full")
    # mode: "auto", "text", "full", "links", "metadata"


# 方式二：一键爬取
result = await quick_crawl("https://example.com", mode="full")

# result:
# {
#   "url": "...",
#   "metadata": {"title": "...", "description": "...", ...},
#   "text": "...",
#   "links": [...],
#   "images": [...],
# }
```

### CLI（给人类或脚本使用）

```bash
# 提取页面纯文本
python -m videosnatch.crawler_cli extract https://example.com --text

# 提取结构化数据
python -m videosnatch.crawler_cli extract https://example.com \
    --rule title h1 --rule price .price --rule description "meta[name=description]::attr(content)"

# 提取表格
python -m videosnatch.crawler_cli extract https://example.com/stats --table table.stats

# 全量爬取
python -m videosnatch.crawler_cli crawl https://example.com --full -o result.json

# 截图
python -m videosnatch.crawler_cli screenshot https://example.com -o page.png

# 批量爬取
python -m videosnatch.crawler_cli batch urls.txt --mode metadata --format jsonl

# 有头模式（调试用）
python -m videosnatch.crawler_cli crawl https://example.com --headed
```

### 完整方法列表

```python
# 生命周期
await crawler.start(headless=True, viewport={"width": 1920, "height": 1080}, stealth=True)
await crawler.close()
crawler.is_running()  # bool

# 导航
await crawler.navigate(url, wait_until="networkidle", timeout=30000)
await crawler.wait_for_stable(timeout_ms=15000)
await crawler.go_back()
await crawler.refresh()
url = await crawler.get_current_url()

# 内容提取
text = await crawler.extract_text(selector=None, max_length=0)
html = await crawler.extract_html(selector=None, outer=True)
links = await crawler.extract_links()  # [{"text","href","title"},...]
images = await crawler.extract_images()  # [{"src","alt","width","height"},...]
structured = await crawler.extract_structured(rules)  # {"field": value, ...}
table = await crawler.extract_table(selector)  # [[col1, col2, ...], ...]
elements = await crawler.extract_all(selector)  # [{"text","html","attrs"},...]
metadata = await crawler.extract_metadata()  # {"title","url","description",...}

# 截图
path = await crawler.screenshot(path="page.png", full_page=True)

# JS 执行
result = await crawler.execute_js("document.title")

# 页面交互
await crawler.wait_for_element(selector, timeout=10000, state="visible")
await crawler.click(selector)
await crawler.fill(selector, value)
await crawler.select_option(selector, value)
text = await crawler.get_text(selector)
attr = await crawler.get_attribute(selector, "href")
steps = await crawler.scroll_to_bottom(step=800, delay=1.0, max_steps=50)

# Cookie 管理
cookies = await crawler.get_cookies()
await crawler.set_cookies([...])
await crawler.clear_cookies()

# 智能提取
result = await crawler.extract(mode="auto")
```

## 重要说明

1. **与视频浏览器共享 profile** — crawler 默认使用同一个 `browser_profile/` 目录，登录态互通。在 GUI 浏览器中登录的网站，爬虫可以直接访问。

2. **stealth 反检测** — 默认启用 CloakBrowser 的 stealth_args 参数，并注入额外的 anti-detection JS 脚本，隐藏 headless 特征。

3. **dependency** — 依赖 `cloakbrowser`, `playwright`, `httpx`，这些与主浏览器共用，无需额外安装。

4. **profile 隔离** — 如果不想共享登录态，传入不同的 `user_data_dir` 即可：
   ```python
   Crawler(user_data_dir="/path/to/custom_profile")
   ```

5. **视频下载** — 如果爬取过程中需要下载视频，仍然可以使用原有的 `videosnatch.downloader` 模块。
