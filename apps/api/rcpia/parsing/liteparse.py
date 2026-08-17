"""LiteParse — local parsing, not implemented yet.

Reserved for deployments that must not send dossiers outside the hospital, which is the
expected production shape once RCPia moves to an internal server. Selecting it fails
immediately and says so, rather than silently falling back to the cloud provider: a
deployment that chose local parsing for confidentiality reasons must never quietly parse
somewhere else.
"""

from rcpia.parsing.base import ParsedDocument, ParseRequest, ParserNotConfigured


class LiteParseParser:
    name = "liteparse"

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        raise ParserNotConfigured(
            "PARSER_PROVIDER=liteparse n'est pas encore disponible. "
            "Utilisez PARSER_PROVIDER=llamaparse, ou attendez l'analyse locale."
        )
