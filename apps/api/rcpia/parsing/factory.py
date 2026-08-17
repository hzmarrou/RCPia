"""Parser selection by configuration."""

from functools import lru_cache

from rcpia.core.config import Settings, get_settings
from rcpia.parsing.base import DocumentParser, ParserNotConfigured
from rcpia.parsing.liteparse import LiteParseParser
from rcpia.parsing.llamaparse import LlamaParseParser


def build_parser(settings: Settings) -> DocumentParser:
    if settings.parser_provider == "llamaparse":
        return LlamaParseParser(settings.llamaparse_api_key)
    if settings.parser_provider == "liteparse":
        return LiteParseParser()
    raise ParserNotConfigured(f"Unknown PARSER_PROVIDER: {settings.parser_provider!r}")


@lru_cache
def get_parser() -> DocumentParser:
    return build_parser(get_settings())
