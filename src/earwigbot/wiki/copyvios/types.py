from __future__ import annotations

from dataclasses import dataclass

from earwigbot.wiki.copyvios.parsers import SourceParser


@dataclass(frozen=True)
class OpenedURL:
    content: bytes
    parser_class: type[SourceParser]


@dataclass(frozen=True)
class Source:
    url: str
    title: str|None
