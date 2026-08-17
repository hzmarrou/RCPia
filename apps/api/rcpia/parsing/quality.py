"""Parser quality checks.

A parse that returns almost nothing is worse than one that fails outright: the failure is
visible, while a near-empty parse quietly becomes a case ledger full of missing fields
that looks like a thin dossier rather than a broken extraction.

These checks produce warnings shown to the uploader, in French, alongside the document.
They never discard a parse — the human decides whether to re-upload.
"""

from rcpia.parsing.base import ParsedDocument

# A one-page dossier extract still runs to a few thousand characters; the reference
# bladder dossier is about 5,600. Well under a thousand means something went wrong.
MIN_EXPECTED_CHARACTERS = 1000
# Scanned documents parse into images with no text behind them.
MIN_TEXT_BLOCK_RATIO = 0.3
# Page numbers above this are treated as provider noise rather than real pages. No
# clinical dossier runs to five thousand pages.
MAX_TRACKED_PAGE = 5000


def quality_warnings(document: ParsedDocument) -> list[str]:
    """Return French warnings describing anything suspicious about a parse."""
    warnings: list[str] = []

    if not document.blocks:
        warnings.append("Aucun contenu n'a pu être extrait de ce document.")
        return warnings

    if document.text_length == 0:
        warnings.append("Le document a été analysé mais ne contient aucun texte exploitable.")
        return warnings

    if document.text_length < MIN_EXPECTED_CHARACTERS:
        warnings.append(
            f"Très peu de texte extrait ({document.text_length} caractères). "
            "Le document est peut-être scanné ou protégé."
        )

    text_blocks = [block for block in document.blocks if block.text.strip()]
    if len(text_blocks) / len(document.blocks) < MIN_TEXT_BLOCK_RATIO:
        warnings.append(
            "La plupart des éléments extraits sont vides ou non textuels "
            "(document probablement scanné)."
        )

    # Page numbers come from the provider, so they are untrusted input. Bounding them
    # keeps a malformed response (page: 10**9) from being turned into a huge range.
    pages = {
        block.page
        for block in document.blocks
        if block.page is not None and 1 <= block.page <= MAX_TRACKED_PAGE
    }
    if not pages:
        warnings.append(
            "Aucune page n'a été identifiée : la traçabilité des données extraites sera "
            "limitée."
        )
        return warnings

    highest = max(pages)
    if highest > len(pages):
        missing = [page for page in range(1, highest + 1) if page not in pages]
        warnings.append(
            "Certaines pages n'ont produit aucun contenu : "
            + ", ".join(str(page) for page in missing[:10])
            + ("…" if len(missing) > 10 else "")
        )

    return warnings
