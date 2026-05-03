# Copyright (C) 2009-2024 Ben Kurtovic <ben.kurtovic@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from __future__ import annotations

__all__ = [
    "DEFAULT_DEGREE",
    "CopyvioChecker",
    "CopyvioCheckResult",
    "globalize",
    "localize",
]

import functools
import logging
import time
import typing
import urllib.request
from collections.abc import Callable
from urllib.request import OpenerDirector

from earwigbot.wiki.copyvios.eds import EDSHelper
from earwigbot.wiki.copyvios.exclusions import ExclusionsDB
from earwigbot.wiki.copyvios.markov import DEFAULT_DEGREE, MarkovChain
from earwigbot.wiki.copyvios.parsers import ArticleParser, ParserArgs
from earwigbot.wiki.copyvios.result import CopyvioCheckResult
from earwigbot.wiki.copyvios.search import SearchEngine, get_search_engine
from earwigbot.wiki.copyvios.workers import CopyvioWorkspace, globalize, localize

if typing.TYPE_CHECKING:
    from earwigbot.wiki.page import Page


class CopyvioChecker:
    """
    Manages the lifecycle of a copyvio check or comparison.

    Created by :py:class:`~earwigbot.wiki.page.Page` and handles the implementation
    details of running a check.
    """

    def __init__(
        self,
        page: Page,
        *,
        min_confidence: float = 0.75,
        max_time: float = 30,
        degree: int = DEFAULT_DEGREE,
        logger: logging.Logger | None = None,
    ) -> None:
        self._page = page
        self._site = page.site
        self._search_config = page.site._search_config
        self._eds_helper = None if page.site._eds_config is None else EDSHelper(
            page.site._eds_config,
            urllib.request.build_opener()
        )
        self._min_confidence = min_confidence
        self._max_time = max_time
        self._degree = degree
        self._logger = logger or logging.getLogger("earwigbot.wiki")

        self._headers = [
            ("User-Agent", page.site.user_agent),
            ("Accept-Encoding", "gzip"),
        ]

        self._parser = ArticleParser(
            self._page.get(),
            lang=self._site.lang,
            nltk_dir=self._search_config["nltk_dir"],
        )
        self._article = MarkovChain(self._parser.strip(), degree=self._degree)

    @functools.cached_property
    def _searcher(self) -> SearchEngine:
        return get_search_engine(self._search_config, self._headers)

    @property
    def _exclusions_db(self) -> ExclusionsDB | None:
        return self._search_config.get("exclusions_db")

    @property
    def article_chain(self) -> MarkovChain:
        return self._article

    def _get_exclusion_callback(self) -> Callable[[str], bool] | None:
        if not self._exclusions_db:
            return None
        return functools.partial(self._exclusions_db.check, self._site.name)

    def run_check(
        self,
        *,
        max_queries: int = 15,
        no_searches: bool = False,
        no_eds: bool = False,
        no_links: bool = False,
        short_circuit: bool = True,
    ) -> CopyvioCheckResult:
        parser_args: ParserArgs = {}
        if self._exclusions_db:
            self._exclusions_db.sync(self._site.name)
            mirror_hints = self._exclusions_db.get_mirror_hints(self._page)
            parser_args["mirror_hints"] = mirror_hints

        workspace = CopyvioWorkspace(
            self._article,
            min_confidence=self._min_confidence,
            max_time=self._max_time,
            logger=self._logger,
            headers=self._headers,
            short_circuit=short_circuit,
            parser_args=parser_args,
            exclusion_callback=self._get_exclusion_callback(),
            search_config=self._search_config,
            eds_helper=self._eds_helper,
            degree=self._degree,
        )

        if self._article.size < 20:  # Auto-fail very small articles
            return workspace.get_result()

        if not no_links:
            workspace.enqueue(self._parser.get_links())
        num_queries_search = 0
        num_queries_eds = 0
        if not no_searches:
            chunks = self._parser.chunk(max_queries)
            for chunk in chunks:
                if short_circuit and workspace.finished:
                    workspace.possible_miss = True
                    break
                self._logger.debug(
                    f"[[{self._page.title}]] -> querying {self._searcher.name} "
                    f"for {chunk!r}"
                )
                workspace.enqueue(self._searcher.search(chunk))
                num_queries_search += 1
                time.sleep(1)  # TODO: Check whether this is needed
        if not no_eds and self._eds_helper is not None:
            chunks = self._parser.chunk(max_queries)
            for chunk in chunks:
                if short_circuit and workspace.finished:
                    workspace.possible_miss = True
                    break
                self._logger.debug(
                    f"[[{self._page.title}]] -> querying EDS for {chunk!r}"
                )
                auth_token, session_token = self._eds_helper.start_session()
                workspace.enqueue_sources(self._eds_helper.search(auth_token, session_token, chunk))
                self._eds_helper.end_session(auth_token, session_token)
                num_queries_eds += 1

        workspace.wait()
        return workspace.get_result(num_queries_search)

    def run_compare(self, urls: list[str]) -> CopyvioCheckResult:
        workspace = CopyvioWorkspace(
            self._article,
            min_confidence=self._min_confidence,
            max_time=self._max_time,
            logger=self._logger,
            headers=self._headers,
            url_timeout=self._max_time,
            num_workers=min(len(urls), 8),
            short_circuit=False,
            search_config=self._search_config,
            eds_helper=self._eds_helper,
            degree=self._degree,
        )

        workspace.enqueue(urls)
        workspace.wait()
        return workspace.get_result()
