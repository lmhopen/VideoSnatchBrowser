#!/usr/bin/env python3
"""VideoSnatch Crawler CLI - 命令行爬虫工具

基于 CloakBrowser + Playwright 的智能爬虫命令行接口。
和视频下载浏览器共享同一个 browser_profile，登录态互通。

用法:
    python -m videosnatch.crawler_cli extract https://example.com
    python -m videosnatch.crawler_cli crawl https://example.com --full
    python -m videosnatch.crawler_cli batch urls.txt -o result.jsonl
"""

import asyncio
import csv
import json
import logging
import sys
import time
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from videosnatch.crawler import Crawler


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)


# ══════════════════════════════════════════════════
# CLI 定义
# ══════════════════════════════════════════════════

def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="videosnatch-crawl",
        description="VideoSnatch Crawler - 基于 CloakBrowser 的智能爬虫",
        formatter_class=RawDescriptionHelpFormatter,
        epilog="""
使用示例:

  # 提取页面纯文本
  python -m videosnatch.crawler_cli extract https://example.com --text

  # 提取结构化数据（按 CSS 规则）
  python -m videosnatch.crawler_cli extract https://example.com \\
      --rule title h1 --rule price .price --rule image img::attr(src)

  # 全量爬取（文本+链接+图片）
  python -m videosnatch.crawler_cli crawl https://example.com --full

  # 截图
  python -m videosnatch.crawler_cli screenshot https://example.com

  # 批量爬取 URL 列表
  python -m videosnatch.crawler_cli batch urls.txt -o results.jsonl

  # 有头模式（可视化调试反爬逻辑）
  python -m videosnatch.crawler_cli crawl https://example.com --headed
        """,
    )

    # 全局参数
    parser.add_argument(
        "--headed", action="store_true",
        help="有头模式（显示浏览器窗口，默认 headless）"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="详细日志"
    )
    parser.add_argument(
        "--profile",
        help="浏览器 profile 目录（默认: 项目 browser_profile/）"
    )
    parser.add_argument(
        "--timeout", type=int, default=30000,
        help="页面加载超时毫秒（默认: 30000）"
    )
    parser.add_argument(
        "--output", "-o",
        help="输出文件路径"
    )

    # 子命令
    sub = parser.add_subparsers(dest="command", required=True)

    # --- crawl ---
    p_crawl = sub.add_parser(
        "crawl", help="爬取页面（自动提取元数据 + 可选全量）"
    )
    p_crawl.add_argument("url", help="目标 URL")
    p_crawl.add_argument(
        "--full", action="store_true",
        help="全量提取（文本 + 链接 + 图片 + 元数据）"
    )
    p_crawl.add_argument(
        "--screenshot",
        help="同时截图并保存到指定路径"
    )

    # --- extract ---
    p_extract = sub.add_parser(
        "extract", help="提取页面数据（支持结构化规则）"
    )
    p_extract.add_argument("url", help="目标 URL")
    p_extract.add_argument(
        "--rule", "-r", action="append", nargs=2,
        metavar=("KEY", "SELECTOR"),
        help="提取规则，例如: --rule title h1 --rule price .price"
    )
    p_extract.add_argument(
        "--selector", "-s",
        help="CSS 选择器（提取所有匹配元素的文本和属性）"
    )
    p_extract.add_argument(
        "--table",
        help="提取表格（CSS 选择器），输出 CSV"
    )
    p_extract.add_argument(
        "--links", action="store_true",
        help="提取所有链接"
    )
    p_extract.add_argument(
        "--images", action="store_true",
        help="提取所有图片"
    )
    p_extract.add_argument(
        "--text", action="store_true",
        help="提取纯文本"
    )
    p_extract.add_argument(
        "--metadata", action="store_true",
        help="提取页面元数据"
    )
    p_extract.add_argument(
        "--format", choices=["json", "text"], default="json",
        help="输出格式（默认: json）"
    )

    # --- screenshot ---
    p_shot = sub.add_parser(
        "screenshot", help="页面截图"
    )
    p_shot.add_argument("url", help="目标 URL")
    p_shot.add_argument(
        "--no-full-page", action="store_false", dest="full_page",
        help="仅截可视区域（默认全页截图）"
    )

    # --- batch ---
    p_batch = sub.add_parser(
        "batch", help="批量爬取 URL 列表文件"
    )
    p_batch.add_argument(
        "input", help="URL 列表文件（每行一个 URL，# 开头的行为注释）"
    )
    p_batch.add_argument(
        "--mode",
        choices=["metadata", "text", "full"],
        default="metadata",
        help="提取模式（默认: metadata）"
    )
    p_batch.add_argument(
        "--format",
        choices=["jsonl", "csv", "json"],
        default="jsonl",
        help="输出格式（默认: jsonl）"
    )
    p_batch.add_argument(
        "--concurrent", type=int, default=3,
        help="并发数（默认: 3）"
    )

    return parser


# ══════════════════════════════════════════════════
# 命令处理
# ══════════════════════════════════════════════════

def _output(data, filepath: str = None):
    """输出数据到文件或 stdout"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if filepath:
        Path(filepath).write_text(text, encoding="utf-8")
        print(f"✓ 已保存到: {filepath}", file=sys.stderr)
    else:
        print(text)


async def _cmd_crawl(crawler: Crawler, args):
    """处理 crawl 子命令"""
    url = await crawler.navigate(args.url, timeout=args.timeout)
    print(f"✓ 已导航到: {url}", file=sys.stderr)

    data = {
        "url": url,
        "metadata": await crawler.extract_metadata(),
    }

    if args.full:
        data["text"] = await crawler.extract_text()
        data["links"] = await crawler.extract_links()
        data["images"] = await crawler.extract_images()

    if args.screenshot:
        path = await crawler.screenshot(args.screenshot)
        data["screenshot"] = path
        print(f"✓ 截图已保存: {path}", file=sys.stderr)

    _output(data, args.output)


async def _cmd_extract(crawler: Crawler, args):
    """处理 extract 子命令"""
    url = await crawler.navigate(args.url, timeout=args.timeout)
    result = {"url": url}

    if args.metadata:
        result["metadata"] = await crawler.extract_metadata()
    if args.text:
        result["text"] = await crawler.extract_text()
    if args.links:
        result["links"] = await crawler.extract_links()
    if args.images:
        result["images"] = await crawler.extract_images()
    if args.rule:
        rules = {k: v for k, v in args.rule}
        result["data"] = await crawler.extract_structured(rules)
    if args.table:
        table = await crawler.extract_table(args.table)
        result["table"] = table
        # 如果指定了输出文件且格式是 CSV，额外写 CSV
        if args.output and args.output.endswith(".csv"):
            import csv as _csv
            with open(args.output, "w", encoding="utf-8-sig",
                      newline="") as f:
                w = _csv.writer(f)
                w.writerows(table)
            print(f"✓ 表格已保存为 CSV: {args.output}", file=sys.stderr)
            return
    if args.selector:
        result["elements"] = await crawler.extract_all(args.selector)

    # 纯文本输出
    if args.format == "text":
        for key, value in result.items():
            if isinstance(value, str):
                print(f"\n{'='*60}")
                print(f"=== {key} ===")
                print(value)
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                print(f"\n{'='*60}")
                print(f"=== {key} ({len(value)} items) ===")
                for item in value[:30]:
                    print(json.dumps(item, ensure_ascii=False))
            elif isinstance(value, list):
                print(f"\n{'='*60}")
                print(f"=== {key} ({len(value)} items) ===")
                for item in value[:30]:
                    print(f"  - {item}")
        return

    _output(result, args.output)


async def _cmd_screenshot(crawler: Crawler, args):
    """处理 screenshot 子命令"""
    await crawler.navigate(args.url)
    out = args.output or (
        f"screenshot_{int(time.time())}.png"
    )
    path = await crawler.screenshot(out, full_page=args.full_page)
    print(f"✓ 截图已保存: {path}")


async def _cmd_batch(crawler: Crawler, args):
    """处理 batch 子命令"""
    # 读取 URL 列表
    with open(args.input, "r", encoding="utf-8") as f:
        urls = [
            line.strip() for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not urls:
        print("✗ URL 列表为空", file=sys.stderr)
        return

    print(
        f"批量爬取 {len(urls)} 个 URL，并发 {args.concurrent}",
        file=sys.stderr,
    )

    # 确定输出文件
    out = args.output or f"batch_{int(time.time())}.jsonl"

    results = []
    sem = asyncio.Semaphore(args.concurrent)
    ok_count = 0
    err_count = 0

    async def crawl_one(url: str) -> dict:
        nonlocal ok_count, err_count
        async with sem:
            try:
                final_url = await crawler.navigate(
                    url, timeout=args.timeout
                )
                result = {"url": final_url, "status": "ok"}
                if args.mode in ("text", "full"):
                    result["text"] = await crawler.extract_text()
                if args.mode == "full":
                    result["links"] = await crawler.extract_links()
                    result["images"] = await crawler.extract_images()
                if args.mode in ("metadata", "full"):
                    result["metadata"] = await crawler.extract_metadata()
                ok_count += 1
                return result
            except Exception as e:
                err_count += 1
                return {
                    "url": url,
                    "status": "error",
                    "error": str(e),
                }

    for i, url in enumerate(urls, 1):
        result = await crawl_one(url)
        results.append(result)
        print(
            f"  [{i}/{len(urls)}] "
            f"{url[:60]:<60} "
            f"{'✓' if result.get('status') == 'ok' else '✗'}",
            file=sys.stderr,
        )

    # 输出
    if args.format == "jsonl":
        with open(out, "w", encoding="utf-8") as f:
            for r in results:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    elif args.format == "csv":
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "status", "title", "description"])
            for r in results:
                meta = r.get("metadata") or {}
                w.writerow([
                    r.get("url", ""),
                    r.get("status", ""),
                    meta.get("title", "") if isinstance(meta, dict) else "",
                    meta.get("description", "") if isinstance(meta, dict) else "",
                ])
    else:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    print(
        f"\n✓ 完成: {len(results)} URL, "
        f"{ok_count} 成功, {err_count} 失败",
        file=sys.stderr,
    )
    print(f"✓ 已保存到: {out}", file=sys.stderr)


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════

async def _run(args):
    """运行命令"""
    async with Crawler(user_data_dir=args.profile) as crawler:
        try:
            await crawler.start(headless=not args.headed)

            if args.command == "crawl":
                await _cmd_crawl(crawler, args)
            elif args.command == "extract":
                await _cmd_extract(crawler, args)
            elif args.command == "screenshot":
                await _cmd_screenshot(crawler, args)
            elif args.command == "batch":
                await _cmd_batch(crawler, args)

        except Exception as e:
            print(f"✗ 错误: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            sys.exit(1)


def main():
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
