"""Built-in web search tool.

The tool is exposed as a normal OpenAI function tool. It mirrors Claude-style
web search interaction semantics without using Anthropic server tools,
provider-specific headers, prompt caching, or SDKs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from typing import Callable, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from pydantic import BaseModel, Field

from ...tool import Tool
from ...types.tools import ToolContext, ToolProgress, ToolResult

MAX_QUERY_LENGTH = 500
MIN_QUERY_LENGTH = 2
DEFAULT_SEARCH_URL = "https://html.duckduckgo.com/html/"


@dataclass(frozen=True)
class SearchResult:
    """One compact source returned to the model."""

    title: str
    url: str
    snippet: str
    page_age: str = ""

    def to_payload(self) -> dict[str, str]:
        payload = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
        }
        if self.page_age:
            payload["page_age"] = self.page_age
        return payload


class SearchBackend(Protocol):
    async def search(
        self,
        *,
        query: str,
        max_results: int,
        user_location: str = "",
    ) -> list[SearchResult]:
        """Return raw search results before tool-level domain filtering."""
        ...


class WebSearchInput(BaseModel):
    query: str = Field(description="Search query for current web information")
    max_results: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum number of results to return, from 1 to 10",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Only include results from these domains or subdomains",
    )
    blocked_domains: list[str] = Field(
        default_factory=list,
        description="Exclude results from these domains or subdomains",
    )
    user_location: str = Field(
        default="",
        description="Optional approximate user location for localized search",
    )


class DuckDuckGoHTMLParser(HTMLParser):
    """Small parser for DuckDuckGo lite/html result pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._seen_urls: set[str] = set()
        self._current_link: dict[str, object] | None = None
        self._current_snippet_tag: str | None = None
        self._current_snippet_parts: list[str] | None = None
        self._fallback_links: list[SearchResult] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.lower(): value or "" for name, value in attrs}
        css_class = attr.get("class", "").lower()

        if tag in {"a", "td", "div", "span"} and _is_snippet_class(css_class):
            self._current_snippet_tag = tag
            self._current_snippet_parts = []
            return

        if tag == "a":
            href = attr.get("href", "")
            url = _extract_result_url(href)
            if not url:
                return
            if _is_result_link_class(css_class):
                self._current_link = {"url": url, "title_parts": []}
            elif _is_external_result_url(url):
                self._fallback_links.append(SearchResult(title="", url=url, snippet=""))
            return

    def handle_data(self, data: str) -> None:
        if self._current_link is not None:
            parts = self._current_link["title_parts"]
            assert isinstance(parts, list)
            parts.append(data)
        elif self._current_snippet_parts is not None:
            self._current_snippet_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_link is not None:
            url = str(self._current_link["url"])
            parts = self._current_link["title_parts"]
            assert isinstance(parts, list)
            title = _clean_text(" ".join(str(part) for part in parts))
            if title and url not in self._seen_urls and _is_external_result_url(url):
                self.results.append(SearchResult(title=title, url=url, snippet=""))
                self._seen_urls.add(url)
            self._current_link = None
            return

        if self._current_snippet_parts is not None and tag == self._current_snippet_tag:
            snippet = _clean_text(" ".join(self._current_snippet_parts))
            if snippet and self.results:
                latest = self.results[-1]
                if not latest.snippet:
                    self.results[-1] = SearchResult(
                        title=latest.title,
                        url=latest.url,
                        snippet=snippet,
                        page_age=latest.page_age,
                    )
            self._current_snippet_tag = None
            self._current_snippet_parts = None

    def close(self) -> None:
        super().close()
        if self.results:
            return
        for item in self._fallback_links:
            if item.url in self._seen_urls:
                continue
            self.results.append(item)
            self._seen_urls.add(item.url)


class DuckDuckGoSearchBackend:
    """Fetch and parse DuckDuckGo's HTML endpoint without extra dependencies."""

    def __init__(
        self,
        *,
        search_url: str = DEFAULT_SEARCH_URL,
        timeout: float = 10.0,
    ) -> None:
        self.search_url = search_url
        self.timeout = timeout

    async def search(
        self,
        *,
        query: str,
        max_results: int,
        user_location: str = "",
    ) -> list[SearchResult]:
        localized_query = query
        if user_location.strip():
            localized_query = f"{query} {user_location.strip()}"

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; CalciferWebSearch/1.0; "
                "+https://github.com/headepic/calcifer)"
            )
        }
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = await client.get(self.search_url, params={"q": localized_query})
            response.raise_for_status()

        return parse_duckduckgo_html(response.text)[:max_results]


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "Search the web for current information. Returns compact source results "
        "with titles, URLs, and snippets so final answers can cite sources."
    )
    parameters = WebSearchInput
    is_concurrency_safe = True
    is_read_only = True
    is_compactable = True
    max_result_size = 20_000
    search_hint = "search current web sources"

    def __init__(self, backend: SearchBackend | None = None) -> None:
        self._backend = backend or DuckDuckGoSearchBackend()

    def is_search_or_read(self, args: dict[str, object]) -> dict[str, bool]:
        return {"is_search": True, "is_read": False, "is_list": False}

    def get_activity_description(self, args: dict[str, object] | None = None) -> str | None:
        if args and args.get("query"):
            return f"Searching web for '{str(args['query'])[:40]}'"
        return "Searching web"

    async def call(
        self,
        args: BaseModel,
        context: ToolContext,
        on_progress: Callable[[ToolProgress], None] | None = None,
    ) -> ToolResult:
        assert isinstance(args, WebSearchInput)
        query = args.query.strip()

        if not query:
            return _error_result("invalid_input", "Search query must not be empty.")
        if len(query) < MIN_QUERY_LENGTH:
            return _error_result(
                "invalid_input",
                f"Search query must be at least {MIN_QUERY_LENGTH} characters.",
            )
        if len(query) > MAX_QUERY_LENGTH:
            return _error_result(
                "query_too_long",
                f"Search query exceeds {MAX_QUERY_LENGTH} characters.",
            )

        start_time = time.monotonic()
        if on_progress:
            on_progress(ToolProgress(
                tool_use_id="",
                type="query_update",
                data={"query": query},
                message=f"Searching: {query}",
            ))

        try:
            results = await self._backend.search(
                query=query,
                max_results=args.max_results,
                user_location=args.user_location,
            )
        except Exception:
            return _error_result(
                "unavailable",
                "Web search is temporarily unavailable.",
            )

        filtered = filter_results_by_domain(
            results,
            allowed_domains=args.allowed_domains,
            blocked_domains=args.blocked_domains,
        )[: args.max_results]
        duration_seconds = round(time.monotonic() - start_time, 3)

        if on_progress:
            on_progress(ToolProgress(
                tool_use_id="",
                type="search_results_received",
                data={"query": query, "result_count": len(filtered)},
                message=f"Found {len(filtered)} results for '{query}'",
            ))

        payload: dict[str, object] = {
            "query": query,
            "search_count": 1,
            "result_count": len(filtered),
            "duration_seconds": duration_seconds,
            "results": [item.to_payload() for item in filtered],
        }
        if filtered:
            payload["message"] = (
                "Use these web search results as sources. After answering, include "
                "a Sources: section with markdown links using each source title and "
                "URL for claims drawn from them."
            )
        else:
            payload["message"] = "No web search results found for this query."

        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


def parse_duckduckgo_html(html: str) -> list[SearchResult]:
    parser = DuckDuckGoHTMLParser()
    parser.feed(html)
    parser.close()
    return parser.results


def filter_results_by_domain(
    results: list[SearchResult],
    *,
    allowed_domains: list[str],
    blocked_domains: list[str],
) -> list[SearchResult]:
    allowed = [_normalize_domain(domain) for domain in allowed_domains]
    blocked = [_normalize_domain(domain) for domain in blocked_domains]
    allowed = [domain for domain in allowed if domain]
    blocked = [domain for domain in blocked if domain]

    filtered: list[SearchResult] = []
    for item in results:
        host = _hostname(item.url)
        if not host:
            continue
        if allowed and not any(_host_matches_domain(host, domain) for domain in allowed):
            continue
        if blocked and any(_host_matches_domain(host, domain) for domain in blocked):
            continue
        filtered.append(item)
    return filtered


def _error_result(error_code: str, message: str) -> ToolResult:
    payload = {
        "type": "web_search_tool_result_error",
        "error_code": error_code,
        "message": message,
    }
    return ToolResult(
        content=json.dumps(payload),
        is_error=True,
        metadata={"error_code": error_code},
    )


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _extract_result_url(href: str) -> str:
    href = unescape(href.strip())
    if not href:
        return ""

    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])

    if href.startswith("//"):
        href = f"https:{href}"
    return href


def _is_result_link_class(css_class: str) -> bool:
    classes = set(css_class.split())
    return bool(classes & {"result-link", "result__a"})


def _is_snippet_class(css_class: str) -> bool:
    classes = set(css_class.split())
    return bool(classes & {"result-snippet", "result__snippet"})


def _is_external_result_url(url: str) -> bool:
    host = _hostname(url)
    if not host:
        return False
    return not _host_matches_domain(host, "duckduckgo.com")


def _hostname(url: str) -> str:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return ""
    return host.lower().strip(".") if host else ""


def _normalize_domain(domain: str) -> str:
    value = domain.strip().lower()
    if not value:
        return ""
    if "://" in value:
        host = urlparse(value).hostname
    else:
        host = urlparse(f"//{value}").hostname
    return host.strip(".") if host else ""


def _host_matches_domain(host: str, domain: str) -> bool:
    host = host.lower().strip(".")
    domain = domain.lower().strip(".")
    return host == domain or host.endswith(f".{domain}")
