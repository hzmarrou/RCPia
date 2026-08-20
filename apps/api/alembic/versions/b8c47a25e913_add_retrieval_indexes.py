"""Add the keyword search vector and the retrieval indexes.

Keyword search is one half of hybrid retrieval; the other half is the vector already stored
on `evidence_chunks.embedding`. Both need an index, and the keyword half needs a `tsvector`
built with the right language and without accents.

**Why two text search configurations.** The corpus is 81% English (EAU, NCCN) and 19% French
(AFU). Stemming French with the English stemmer, or the reverse, produces lexemes that match
neither language properly, so each passage is indexed with the stemmer for the language it is
written in. The configuration is chosen per row from `evidence_chunks.language`.

**Why `unaccent`.** French clinical text is written both `métastatique` and `metastatique`.
Without accent folding those two do not match each other — not across languages, but *within
French*, which is worse. `unaccent` runs as a filtering dictionary ahead of the stemmer, so
the accented and unaccented spellings reduce to one lexeme.

**Why the regconfig literals sit inside the CASE branches.** A generated column must be
immutable. `CASE … END::regconfig` casts the *result*, which is a catalogue lookup at run
time and only STABLE — PostgreSQL rejects it with `generation expression is not immutable`.
Casting each literal instead lets the planner fold it to a constant OID at parse time.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b8c47a25e913"
down_revision: str | None = "79533e4a10c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Named rather than inlined so the model, the migration and the query builder cannot drift
# apart: a `tsvector` built with one configuration and searched with another silently returns
# nothing rather than failing.
FRENCH_CONFIG = "public.rcpia_fr"
ENGLISH_CONFIG = "public.rcpia_en"

SEARCH_VECTOR = (
    f"to_tsvector(CASE language WHEN 'fr' THEN '{FRENCH_CONFIG}'::regconfig "
    f"ELSE '{ENGLISH_CONFIG}'::regconfig END, keyword_text)"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")

    # `unaccent` first, then the stemmer: folding accents after stemming would be too late,
    # because the French stemmer treats `é` and `e` as different letters.
    for name, stemmer in ((FRENCH_CONFIG, "french"), (ENGLISH_CONFIG, "english")):
        op.execute(f"CREATE TEXT SEARCH CONFIGURATION {name} (COPY = {stemmer})")
        op.execute(
            f"ALTER TEXT SEARCH CONFIGURATION {name} "
            f"ALTER MAPPING FOR hword, hword_part, word WITH unaccent, {stemmer}_stem"
        )

    # Generated rather than maintained by the application: a passage whose vector was not
    # refreshed after an edit is invisible to keyword search, which reads exactly like a
    # corpus that does not cover the topic. PostgreSQL also records a dependency on the
    # configurations, so neither can be dropped while the column exists.
    op.execute(
        "ALTER TABLE evidence_chunks ADD COLUMN search_vector tsvector "
        f"GENERATED ALWAYS AS ({SEARCH_VECTOR}) STORED"
    )
    op.execute(
        "CREATE INDEX ix_evidence_chunks_search_vector "
        "ON evidence_chunks USING gin (search_vector)"
    )

    # Cosine distance, matching how the embeddings are compared. HNSW rather than IVFFlat
    # because it does not need a training set and stays correct as the corpus grows; the
    # corpus is small enough today that the index barely matters, and large enough after the
    # full release that adding it later would mean a rebuild under load.
    op.execute(
        "CREATE INDEX ix_evidence_chunks_embedding ON evidence_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_evidence_chunks_embedding")
    op.execute("DROP INDEX IF EXISTS ix_evidence_chunks_search_vector")
    op.execute("ALTER TABLE evidence_chunks DROP COLUMN IF EXISTS search_vector")
    op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {ENGLISH_CONFIG}")
    op.execute(f"DROP TEXT SEARCH CONFIGURATION IF EXISTS {FRENCH_CONFIG}")
    # The extension is left in place, as `vector` was: dropping it would break any other
    # object depending on it, and it costs nothing to keep.
