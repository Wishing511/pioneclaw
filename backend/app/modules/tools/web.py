"""
Web 工具 - 网络搜索和内容获取

包含：
- WebSearchTool: 智能搜索（自动选择引擎）
- WebFetchTool: 获取网页内容

搜索优先级：
1. Brave Search API（需要 API Key，免费每月 2000 次）
2. 搜狗 Sogou（中文查询首选，无需 API Key）
3. Bing（备选，全球可用）
4. DuckDuckGo（免费备选）
"""

import html
import json
import logging
import os
import re
import warnings
from urllib.parse import quote_plus, urlparse

import httpx

from app.core.ssrf_protection import SsrFBlockedError, validate_url_ssrf
from app.modules.tools.base import BaseTool, ToolParameter

logger = logging.getLogger(__name__)

# 禁用 SSL 警告（verify=False 时 httpx 会产生警告）
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# 共享常量
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MAX_REDIRECTS = 5


def _strip_tags(text: str) -> str:
    """移除 HTML 标签并解码实体"""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _normalize(text: str) -> str:
    """规范化空白"""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _validate_url(url: str) -> tuple[bool, str]:
    """验证 URL（含 SSRF 防护）"""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return False, "Only http/https allowed"
        if not p.netloc:
            return False, "Missing domain"
        # SSRF 防护：检查 hostname 是否为内部/私有地址
        validate_url_ssrf(url)
        return True, ""
    except SsrFBlockedError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def _is_valid_brave_key(api_key: str) -> bool:
    """检查 Brave API Key 是否有效"""
    if not api_key or not api_key.strip():
        return False
    try:
        api_key.encode("ascii")
        # 排除明显的占位符
        lower = api_key.lower()
        return not (
            "your" in lower or "api" in lower or "key" in lower or "占位" in lower
        )
    except UnicodeEncodeError:
        return False


# ==================== Brave Search（首选，需要 API Key）====================


class BraveSearchTool(BaseTool):
    """Brave Search API 搜索工具（首选）"""

    name = "brave_search"
    is_parallel_safe = True
    description = "使用 Brave Search API 搜索网络（需要 API Key）"
    parameters = {
        "query": ToolParameter(type="string", description="搜索关键词"),
        "count": ToolParameter(
            type="integer", description="返回结果数量 (1-10)", default=5
        ),
    }
    required = ["query"]

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")

    async def execute(self, query: str, count: int = 5, **kwargs) -> str:
        """执行 Brave 搜索"""
        if not query:
            return "错误: 搜索关键词不能为空"

        if not _is_valid_brave_key(self.api_key):
            return "错误: Brave API Key 无效或未配置"

        try:
            n = min(max(count, 1), 10)
            logger.info(f"[Brave] Searching: {query}")

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": n},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self.api_key,
                    },
                    timeout=10.0,
                )
                response.raise_for_status()

            results = response.json().get("web", {}).get("results", [])
            if not results:
                return f"未找到结果: {query}"

            lines = [f"[Brave Search] 搜索结果: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item.get('title', '无标题')}")
                lines.append(f"   URL: {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   摘要: {desc}")
                lines.append("")

            logger.info(f"[Brave] Search completed: {len(results)} results")
            return "\n".join(lines)

        except httpx.HTTPStatusError as e:
            logger.error(f"[Brave] HTTP error: {e}")
            return f"Brave 搜索 HTTP 错误: {e.response.status_code}"
        except Exception as e:
            logger.error(f"[Brave] Search error: {e}")
            return f"Brave 搜索错误: {e}"


# ==================== DuckDuckGo Search（备选，免费无需 Key）====================


class DuckDuckGoSearchTool(BaseTool):
    """DuckDuckGo 搜索工具（备选，免费无需 API Key）"""

    name = "duckduckgo_search"
    is_parallel_safe = True
    description = "使用 DuckDuckGo 搜索网络（免费，无需 API Key）"
    parameters = {
        "query": ToolParameter(type="string", description="搜索关键词"),
        "count": ToolParameter(
            type="integer", description="返回结果数量 (1-10)", default=5
        ),
    }
    required = ["query"]

    def __init__(self):
        pass

    async def execute(self, query: str, count: int = 5, **kwargs) -> str:
        """执行 DuckDuckGo 搜索（通过 HTML 解析）"""
        if not query:
            return "错误: 搜索关键词不能为空"

        try:
            n = min(max(count, 1), 10)
            logger.info(f"[DuckDuckGo] Searching: {query}")

            # DuckDuckGo HTML 搜索
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                verify=False,
                transport=httpx.AsyncHTTPTransport(retries=2),
            ) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
                response.raise_for_status()

            # 解析 HTML 提取结果
            results = self._parse_results(response.text, n)

            if not results:
                return f"未找到结果: {query}"

            lines = [f"[DuckDuckGo] 搜索结果: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item['title']}")
                lines.append(f"   URL: {item['url']}")
                if item.get("snippet"):
                    lines.append(f"   摘要: {item['snippet']}")
                lines.append("")

            logger.info(f"[DuckDuckGo] Search completed: {len(results)} results")
            return "\n".join(lines)

        except httpx.ConnectTimeout:
            logger.error("[DuckDuckGo] Connection timeout")
            return "网络搜索暂时不可用（连接超时）。建议配置 Brave API Key 获得更稳定的搜索服务：https://brave.com/search/api/"
        except httpx.ConnectError as e:
            logger.error(f"[DuckDuckGo] Connection error: {e}")
            return "网络搜索暂时不可用（网络连接失败）。建议配置 Brave API Key 获得更稳定的搜索服务：https://brave.com/search/api/"
        except Exception as e:
            logger.error(f"[DuckDuckGo] Search error: {e}")
            return f"DuckDuckGo 搜索错误: {e}"

    def _parse_results(self, html_text: str, max_results: int) -> list:
        """解析 DuckDuckGo HTML 结果"""
        results = []

        # 策略：按 result__body 分块，每块包含一个完整结果
        result_blocks = re.split(r'class="result__body"', html_text)
        # 第一个块是页面前导内容，跳过
        for block in result_blocks[1:]:
            if len(results) >= max_results:
                break

            # 提取标题：h2.result__title > a.result__a
            title_match = re.search(
                r'class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE
            )
            if not title_match:
                continue

            title = _strip_tags(title_match.group(1)).strip()
            if not title:
                continue

            # 提取 URL：a.result__url 的 href 属性
            url_match = re.search(
                r'class="result__url"\s+href="([^"]*)"', block, re.IGNORECASE
            )
            if not url_match:
                continue

            url = _strip_tags(url_match.group(1)).strip()

            # 提取摘要：a.result__snippet
            snippet = ""
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</a>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if snippet_match:
                snippet = _strip_tags(snippet_match.group(1)).strip()

            # 处理 URL
            if url.startswith("//"):
                url = "https:" + url
            elif not url.startswith("http"):
                continue

            # 跳过 DuckDuckGo 内部链接
            if "duckduckgo.com/l/" in url and "uddg=" in url:
                from urllib.parse import parse_qs
                from urllib.parse import urlparse as uparse

                parsed = uparse(url)
                qs = parse_qs(parsed.query)
                uddg = qs.get("uddg", [""])[0]
                if uddg:
                    url = uddg

            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet[:200] if snippet else "",
                }
            )

        return results


# ==================== Bing Search（备选，全球可用）====================


class BingSearchTool(BaseTool):
    """Bing 搜索工具（备选，全球可用）"""

    name = "bing_search"
    is_parallel_safe = True
    description = "使用 Bing 搜索网络"
    parameters = {
        "query": ToolParameter(type="string", description="搜索关键词"),
        "count": ToolParameter(
            type="integer", description="返回结果数量 (1-10)", default=5
        ),
    }
    required = ["query"]

    def __init__(self):
        pass

    async def execute(self, query: str, count: int = 5, **kwargs) -> str:
        if not query:
            return "错误: 搜索关键词不能为空"

        try:
            n = min(max(count, 1), 10)
            logger.info(f"[Bing] Searching: {query}")

            # 检测中文查询，设置中国市场参数以获取更好的中文结果
            has_chinese = bool(re.search(r"[\u4e00-\u9fff]", query))
            mkt_param = "&mkt=zh-CN&setlang=zh-cn" if has_chinese else ""

            url = f"https://www.bing.com/search?q={quote_plus(query)}&count={n}{mkt_param}"
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15.0,
                verify=False,
            ) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "Accept-Encoding": "gzip, deflate",
                    },
                )
                response.raise_for_status()

            results = self._parse_bing_results(response.text, n)

            if not results:
                return f"未找到结果: {query}"

            lines = [f"[Bing] 搜索结果: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item['title']}")
                lines.append(f"   URL: {item['url']}")
                if item.get("snippet"):
                    lines.append(f"   摘要: {item['snippet']}")
                lines.append("")

            logger.info(f"[Bing] Search completed: {len(results)} results")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[Bing] Search error: {e}")
            return f"Bing 搜索错误: {e}"

    def _parse_bing_results(self, html_text: str, max_results: int) -> list:
        """解析 Bing 搜索结果"""
        results = []

        blocks = re.split(r'<li\s+class="b_algo"', html_text)
        for block in blocks[1:]:
            if len(results) >= max_results:
                break

            title_match = re.search(
                r'<h2[^>]*>.*?<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if not title_match:
                continue

            url = _strip_tags(title_match.group(1)).strip()
            title = _strip_tags(title_match.group(2)).strip()

            if not title:
                continue

            snippet = ""
            cap_match = re.search(
                r'class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if cap_match:
                snippet = _strip_tags(cap_match.group(1)).strip()
            else:
                p_match = re.search(
                    r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE
                )
                if p_match:
                    snippet = _strip_tags(p_match.group(1)).strip()

            if url and not url.startswith("http"):
                if url.startswith("//"):
                    url = "https:" + url
                else:
                    continue

            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet[:200] if snippet else "",
                }
            )

        return results


# ==================== Sogou Search（中文搜索首选，无需 API Key）====================


class SogouSearchTool(BaseTool):
    """搜狗搜索工具（中文搜索首选，无需 API Key）"""

    name = "sogou_search"
    is_parallel_safe = True
    description = "使用搜狗搜索网络（中文搜索首选）"
    parameters = {
        "query": ToolParameter(type="string", description="搜索关键词"),
        "count": ToolParameter(
            type="integer", description="返回结果数量 (1-10)", default=5
        ),
    }
    required = ["query"]

    def __init__(self):
        pass

    async def execute(self, query: str, count: int = 5, **kwargs) -> str:
        if not query:
            return "错误: 搜索关键词不能为空"

        try:
            n = min(max(count, 1), 10)
            logger.info(f"[Sogou] Searching: {query}")

            url = f"https://www.sogou.com/web?query={quote_plus(query)}"
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15.0,
                verify=False,
            ) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                        "Accept-Encoding": "gzip, deflate",
                        "Cache-Control": "no-cache",
                    },
                )
                response.raise_for_status()

            results = self._parse_sogou_results(response.text, n)

            if not results:
                return f"未找到结果: {query}"

            lines = [f"[搜狗] 搜索结果: {query}\n"]
            for i, item in enumerate(results[:n], 1):
                lines.append(f"{i}. {item['title']}")
                lines.append(f"   URL: {item['url']}")
                if item.get("snippet"):
                    lines.append(f"   摘要: {item['snippet']}")
                lines.append("")

            logger.info(f"[Sogou] Search completed: {len(results)} results")
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[Sogou] Search error: {e}")
            return f"搜狗搜索错误: {e}"

    def _parse_sogou_results(self, html_text: str, max_results: int) -> list:
        """解析搜狗搜索结果"""
        results = []

        # 搜狗使用 <div class="vrwrap"> 包裹每个结果
        blocks = re.split(r'<div[^>]*class="[^"]*vrwrap[^"]*"', html_text)
        if len(blocks) < 2:
            # 备用：尝试 rb 类
            blocks = re.split(r'<div[^>]*class="[^"]*rb[^"]*"', html_text)

        for block in blocks[1:]:
            if len(results) >= max_results:
                break

            # 提取标题和 URL: <h3 class="vrTitle">...<a href="...">Title</a>...</h3>
            title_match = re.search(
                r'<h3[^>]*>.*?<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if not title_match:
                # 备用：直接找 <a> 标签
                title_match = re.search(
                    r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
                    block,
                    re.DOTALL | re.IGNORECASE,
                )

            if not title_match:
                continue

            url = _strip_tags(title_match.group(1)).strip()
            title = _strip_tags(title_match.group(2)).strip()

            if not title or not url:
                continue

            # 跳过搜狗内部链接和非网页结果
            if "sogou.com" in url and "/link?" in url:
                # 搜狗跳转链接，需要提取真实 URL
                from urllib.parse import parse_qs
                from urllib.parse import urlparse as uparse

                parsed = uparse(url)
                qs = parse_qs(parsed.query)
                real_url = qs.get("url", [""])[0] or qs.get("aurl", [""])[0]
                if real_url:
                    url = real_url

            # 提取摘要
            snippet = ""
            snip_match = re.search(
                r'<p[^>]*class="[^"]*(?:star-wiki|str_info|str-text|space-txt|str_info_inner)[^"]*"[^>]*>(.*?)</p>',
                block,
                re.DOTALL | re.IGNORECASE,
            )
            if not snip_match:
                # 更宽松的摘要匹配
                snip_match = re.search(
                    r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE
                )
            if snip_match:
                snippet = _strip_tags(snip_match.group(1)).strip()

            if url and not url.startswith("http"):
                if url.startswith("//"):
                    url = "https:" + url
                else:
                    continue

            results.append(
                {
                    "title": title,
                    "url": url,
                    "snippet": snippet[:200] if snippet else "",
                }
            )

        return results


# ==================== 智能搜索工具（自动选择引擎）====================


class WebSearchTool(BaseTool):
    """
    Web 搜索工具 - 智能选择搜索引擎

    优先级：
    1. Brave Search API（如果有有效的 API Key）
    2. 搜狗 Sogou（中文查询首选，无需 API Key）
    3. Bing（备选，全球可用）
    4. DuckDuckGo（免费备选）

    注意：required 设为空，参数验证在 execute 内自行处理，
    以兼容 LLM 使用不同的参数名（如 q / search_term 等）
    """

    name = "web_search"
    is_parallel_safe = True
    description = (
        "搜索网络获取最新信息。返回标题、URL 和摘要。"
        "搜索摘要通常已包含足够信息，直接基于摘要回答即可，无需额外调用 web_fetch。"
        "如需最新年份的数据，请在 query 中包含当前年份。回答时必须标注信息来源"
    )
    parameters = {
        "query": ToolParameter(
            type="string",
            description="搜索关键词（必填）",
        ),
        "count": ToolParameter(
            type="integer",
            description="返回结果数量 (1-10)",
            default=5,
        ),
    }
    required = []

    def __init__(self, api_key: str | None = None, max_results: int = 5):
        self.api_key = api_key or os.environ.get("BRAVE_API_KEY", "")
        self.max_results = max_results
        self._brave = (
            BraveSearchTool(self.api_key) if _is_valid_brave_key(self.api_key) else None
        )
        self._sogou = SogouSearchTool()
        self._bing = BingSearchTool()
        self._duckduckgo = DuckDuckGoSearchTool()

    async def execute(self, **kwargs) -> str:
        """执行 Web 搜索（智能选择引擎），兼容多种参数名"""
        # 兼容 LLM 可能使用的各种参数名
        query = (
            kwargs.get("query")
            or kwargs.get("q")
            or kwargs.get("search")
            or kwargs.get("keyword")
            or kwargs.get("keywords")
            or kwargs.get("search_term")
            or kwargs.get("term")
            or kwargs.get("question")
            or ""
        ).strip()

        # 如果所有别名都不匹配，尝试用 kwargs 中任意非空字符串值作为查询
        if not query:
            for key, val in kwargs.items():
                if key in ("count", "n", "num", "limit", "max_results"):
                    continue
                if val and isinstance(val, str) and val.strip():
                    query = val.strip()
                    logger.info(
                        f"[WebSearch] Using '{key}' as fallback query: {query[:50]}"
                    )
                    break

        count = (
            kwargs.get("count")
            or kwargs.get("n")
            or kwargs.get("num")
            or kwargs.get("limit")
            or 5
        )
        if isinstance(count, str) and count.isdigit():
            count = int(count)
        elif not isinstance(count, int):
            count = 5

        if not query:
            received = {k: str(v)[:50] for k, v in kwargs.items()}
            return (
                f"错误: 搜索关键词不能为空。收到的参数: {received}。"
                f"请提供 query 参数，例如 web_search(query='最新的AI一体机咨询')"
            )

        # 清理搜索运算符（搜狗等引擎不支持 site:/OR 等高级语法）
        original_query = query
        query = re.sub(r"\bOR\b", "", query)  # 移除 OR 运算符
        query = re.sub(r"\bsite:\S+", "", query)  # 移除 site: 过滤
        query = re.sub(r"\s{2,}", " ", query).strip()  # 合并多余空格
        if query != original_query:
            logger.info(f"[WebSearch] Cleaned query: '{original_query}' -> '{query}'")

        n = count or self.max_results

        # 优先使用 Brave（如果配置了有效 Key）
        if self._brave:
            logger.info("Using Brave Search (primary)")
            result = await self._brave.execute(query=query, count=n)
            if not result.startswith("错误") and not result.startswith("Brave 搜索"):
                return result
            logger.warning(f"Brave failed, falling back: {result[:100]}")

        # 优先使用搜狗（中文搜索效果好，且无需 API Key）
        logger.info("Using Sogou (primary)")
        result = await self._sogou.execute(query=query, count=n)
        if not result.startswith("错误") and not result.startswith("搜狗搜索"):
            if not result.startswith("未找到结果"):
                return result
        logger.warning(f"Sogou failed or no results, falling back: {result[:100]}")

        # 检测中文查询也记录（搜狗已覆盖，但保留日志）
        bool(re.search(r"[\u4e00-\u9fff]", query))

        # 使用 Bing
        logger.info("Using Bing")
        result = await self._bing.execute(query=query, count=n)
        if not result.startswith("错误") and not result.startswith("Bing 搜索"):
            if not result.startswith("未找到结果"):
                return result
        logger.warning(
            f"Bing failed or no results, falling back to DuckDuckGo: {result[:100]}"
        )

        # 使用 DuckDuckGo
        logger.info("Using DuckDuckGo (last resort)")
        return await self._duckduckgo.execute(query=query, count=n)


# ==================== Web 内容获取工具 ====================


class WebFetchTool(BaseTool):
    """Web 内容获取工具（含安全防护 + 缓存）"""

    name = "web_fetch"
    is_parallel_safe = True
    description = (
        "获取指定网页内容并提取相关信息。仅在 web_search 摘要确实不足以回答问题时使用。"
        "需要提供 prompt 说明你想从页面中提取什么信息。"
        "注意：认证页面会失败；中国新闻/政府网站经常拦截"
    )
    parameters = {
        "url": ToolParameter(
            type="string",
            description="要获取的网页 URL（必填）",
        ),
        "prompt": ToolParameter(
            type="string",
            description="你想从页面中提取什么信息（必填）。例如：提取天气数据、找到版本号、总结文章要点",
        ),
        "max_chars": ToolParameter(
            type="integer",
            description="最大字符数",
            default=10000,
        ),
    }
    required = []

    FETCH_HEADERS = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    def __init__(self, max_chars: int = 10000):
        self.max_chars = min(max_chars, 50000)  # 硬上限 50KB

    async def execute(self, **kwargs) -> str:
        """获取网页内容，兼容多种参数名"""
        url = (
            kwargs.get("url")
            or kwargs.get("link")
            or kwargs.get("address")
            or kwargs.get("href")
            or kwargs.get("webpage")
            or ""
        ).strip()

        # 如果所有别名都不匹配，尝试用 kwargs 中以 http 开头的值作为 URL
        if not url:
            for key, val in kwargs.items():
                if key in ("max_chars", "limit", "max_length"):
                    continue
                if val and isinstance(val, str) and val.strip().startswith("http"):
                    url = val.strip()
                    logger.info(f"[WebFetch] Using '{key}' as fallback URL: {url[:80]}")
                    break

        # 最后的兜底：使用任意非空字符串值
        if not url:
            for key, val in kwargs.items():
                if key in ("max_chars", "limit", "max_length"):
                    continue
                if val and isinstance(val, str) and val.strip():
                    url = val.strip()
                    logger.info(
                        f"[WebFetch] Using '{key}' as fallback URL (non-http): {url[:80]}"
                    )
                    break

        max_chars = (
            kwargs.get("max_chars")
            or kwargs.get("limit")
            or kwargs.get("max_length")
            or self.max_chars
        )
        if isinstance(max_chars, str) and max_chars.isdigit():
            max_chars = int(max_chars)
        elif not isinstance(max_chars, int):
            max_chars = self.max_chars

        if not url:
            received = {k: str(v)[:50] for k, v in kwargs.items()}
            return (
                f"错误: URL 不能为空。收到的参数: {received}。"
                f"请提供 url 参数，例如 web_fetch(url='https://example.com')"
            )

        # 验证 URL
        is_valid, error_msg = _validate_url(url)
        if not is_valid:
            return f"URL 验证失败: {error_msg}"

        try:
            logger.info(f"Fetching URL: {url}")

            async with httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_REDIRECTS,
                timeout=30.0,
                verify=False,  # 部分中文站点证书不规范，跳过验证
            ) as client:
                response = await client.get(url, headers=self.FETCH_HEADERS)
                response.raise_for_status()

            ctype = response.headers.get("content-type", "")

            # JSON 响应
            if "application/json" in ctype:
                text = json.dumps(response.json(), indent=2, ensure_ascii=False)
            # HTML 响应
            elif "text/html" in ctype or response.text[:256].strip().lower().startswith(
                ("<!doctype", "<html")
            ):
                text = self._extract_text(response.text, url)
            else:
                text = response.text

            # 如果提取结果太短（<100 字符），可能提取失败，返回更多原文
            if len(text.strip()) < 100:
                logger.warning(
                    f"[WebFetch] Extracted text too short ({len(text)} chars), returning raw text"
                )
                # 回退：从全文提取
                raw = _normalize(re.sub(r"<[^>]+>", " ", response.text))
                raw = html.unescape(raw)
                text = _normalize(raw)

            # 截断
            if len(text) > max_chars:
                text = text[:max_chars] + "\n... (内容已截断)"

            logger.info(f"Fetched URL: {url} ({len(text)} characters)")
            return text

        except httpx.ConnectTimeout:
            logger.error(f"[WebFetch] Connection timeout: {url}")
            return f"获取网页超时: {url}（服务器无响应或网络不通）"
        except httpx.ConnectError as e:
            logger.error(f"[WebFetch] Connection error: {url} - {e}")
            return f"获取网页连接失败: {url}（DNS 解析失败或网络不通）"
        except httpx.HTTPStatusError as e:
            logger.error(f"[WebFetch] HTTP error {e.response.status_code}: {url}")
            return f"获取网页 HTTP {e.response.status_code} 错误: {url}"
        except Exception as e:
            logger.error(f"Web fetch error for {url}: {e}")
            return f"获取网页错误 ({url}): {e}"

    def _extract_text(self, html_content: str, url: str = "") -> str:
        """提取 HTML 中的文本，优先定位文章正文区域"""
        # 移除脚本和样式
        html_text = re.sub(r"<script[\s\S]*?</script>", "", html_content, flags=re.I)
        html_text = re.sub(r"<style[\s\S]*?</style>", "", html_text, flags=re.I)
        # 移除注释
        html_text = re.sub(r"<!--[\s\S]*?-->", "", html_text)
        # 移除 nav/footer/header 等非正文区域（粗略清理）
        for tag in ("nav", "footer", "header", "aside"):
            html_text = re.sub(rf"<{tag}[\s\S]*?</{tag}>", "", html_text, flags=re.I)

        # 尝试定位文章正文区域
        content_html = ""
        patterns = [
            # 微信公众号
            r'<div[^>]*id="js_content"[^>]*>([\s\S]*?)</div>',
            # HTML5 article
            r"<article[^>]*>([\s\S]*?)</article>",
            # 常见文章容器 class
            r'<div[^>]*class="[^"]*article-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*article-body[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*article[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*post-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*post-body[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*entry-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*entry[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*news-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*news-text[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*news-body[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*main-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*text[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*body[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*detail[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*class="[^"]*rich_media_content[^"]*"[^>]*>([\s\S]*?)</div>',
            # 常见文章容器 id
            r'<div[^>]*id="article[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*id="content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*id="main-content[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*id="text[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*id="detail[^"]*"[^>]*>([\s\S]*?)</div>',
            r'<div[^>]*id="js_article[^"]*"[^>]*>([\s\S]*?)</div>',
            # 通用：section 标签
            r"<section[^>]*>([\s\S]*?)</section>",
        ]

        for pattern in patterns:
            m = re.search(pattern, html_text, re.DOTALL | re.IGNORECASE)
            if m:
                extracted = m.group(1).strip()
                # 至少 100 字符才算有效（比之前的 200 更宽松）
                if len(re.sub(r"<[^>]+>", "", extracted).strip()) > 100:
                    content_html = extracted
                    logger.info(
                        f"[WebFetch] Found article content with pattern: {pattern[:60]}..., {len(content_html)} html chars"
                    )
                    break

        # 如果没找到文章区域，用全文
        if not content_html:
            content_html = html_text

        # 移除标签
        text = re.sub(r"<[^>]+>", " ", content_html)
        # 解码实体
        text = html.unescape(text)
        # 去除每行首尾空白但保留段落间距
        text = "\n".join(line.strip() for line in text.splitlines())
        # 规范化空白
        text = _normalize(text)

        # 过滤微信/网页常见外壳文字和导航/页脚噪音
        junk_patterns = [
            r"系统出错[，。\s]*",
            r"视频\s*小程序\s*赞[，。\s]*",
            r"轻点两下取消赞[，。\s]*",
            r"在看[，。\s]*轻点两下取消在看[，。\s]*",
            r"分享\s*留言\s*收藏\s*听过",
            r"网页链接[：:]\s*\S*",
            r"扫描二维码[，。\s]*",
            r"关注公众号[，。\s]*",
            r"阅读\s*\d+[，。\s]*",
            r"投诉[，。\s]*",
            r"写留言[，。\s]*",
            r"Copyright\s*©.*?(?=\n|$)",
            r"版权所有[，。：:\s]*\S*",
            r"沪ICP备\S*",
            r"京ICP备\S*",
            r"粤ICP备\S*",
            r"免责声明[：:]\s*\S*",
            r"广告[：:\s]*",
            r"点击上方\S*关注[，。\s]*",
            r"点击蓝字\S*关注[，。\s]*",
            r"↑?\s*点击\s*上方\s*\S*\s*关注\s*",
            r"长按二维码\S*",
            r"点击\s*上方\s*蓝字\s*关注",
            r"收录于话题\S*",
            r"阅读原文\s*$",
            r"举报\s*$",
            r"分享到\s*$",
            r"赞\s*\d+\s*$",
            r"在看\s*\d+\s*$",
        ]
        for pat in junk_patterns:
            text = re.sub(pat, "", text, flags=re.MULTILINE)

        # 清理过多空行
        text = re.sub(r"\n{4,}", "\n\n\n", text)

        return text.strip()
