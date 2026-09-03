"""Parser contract.

Parsing turns an uploaded dossier into text blocks that extracted facts can cite. Every
block carries where it came from — page, section, and bounding box when the provider
supplies them — because a fact without provenance cannot be stored.

Two providers exist: LlamaParse Cloud (the v1 default, already validated by the user on
real dossiers) and LiteParse (a future local option for deployments that must not send
documents outside the hospital). They are interchangeable behind this interface.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

BlockType = Literal["paragraph", "table", "heading", "image", "unknown"]


class ParserError(Exception):
    """Base class for parser failures."""


class ParserNotConfigured(ParserError):
    """The selected provider is missing configuration, or is not implemented yet."""


class ParserFailed(ParserError):
    """The provider was reachable but could not parse the document."""


class ParsedBlock(BaseModel):
    """One unit of parsed content, with wherever it came from attached."""

    text: str
    page: int | None = None
    section: str | None = None
    bbox: list[float] | None = None
    block_type: BlockType = "unknown"


class ParsedDocument(BaseModel):
    provider: str
    provider_version: str | None = None
    blocks: list[ParsedBlock] = Field(default_factory=list)
    raw_text: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def text_length(self) -> int:
        return len(self.raw_text)


class ParsedMarkdown(BaseModel):
    """A document converted to markdown, for the corpus rather than for a dossier.

    `ParsedDocument` above exists so an extracted fact can cite a page and a bounding box.
    The corpus needs something different: the heading structure, because stage 1 recovers a
    section path from `#` levels and routes passages by it. Markdown is the provider output
    that carries headings; the JSON result carries provenance and flattens them.

    So both shapes are fetched from the same provider, and neither is a lossy version of the
    other -- they answer different questions about the same PDF.
    """

    provider: str
    provider_version: str | None = None
    markdown: str = ""
    job_id: str | None = None


@dataclass(frozen=True)
class ParseRequest:
    """What a provider needs in order to parse one document."""

    filename: str
    content: bytes
    content_type: str
    # Dossiers are French; providers that accept a language hint parse them better.
    language: str = "fr"
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class MarkdownParser(Protocol):
    """Providers that can also return markdown.

    Deliberately separate from `DocumentParser`. LiteParse exists so a hospital that must not
    send documents outside its walls still has a dossier path; requiring it to produce corpus
    markdown as well would make the corpus builder look portable when it is not. The corpus is
    built once, by us, from published guidelines -- not on a hospital's own machine.
    """

    name: str

    async def parse_markdown(self, request: ParseRequest) -> ParsedMarkdown:
        """Convert a document to markdown, or raise a ParserError."""
        ...


@runtime_checkable
class DocumentParser(Protocol):
    name: str

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        """Parse a document, or raise a ParserError."""
        ...
