"""Small no-key web-search adapter for time-sensitive questions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class WebSearcher:
    def __init__(
        self,
        *,
        max_results: int = 5,
        timeout_seconds: float = 8.0,
        region: str = "in-en",
    ) -> None:
        self.max_results = max_results
        self.timeout_seconds = timeout_seconds
        self.region = region

    def search(self, query: str) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError as error:
            raise RuntimeError("DDGS is missing; run `uv sync`.") from error

        raw_results = DDGS(timeout=int(self.timeout_seconds)).text(
            query,
            region=self.region,
            safesearch="moderate",
            max_results=self.max_results,
            backend="auto",
        )
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            url = str(item.get("href", "")).strip()
            snippet = str(item.get("body", "")).strip()
            if not title or not url:
                continue
            results.append(
                SearchResult(
                    title=title[:200],
                    url=url[:500],
                    snippet=snippet[:700],
                )
            )
        return results
