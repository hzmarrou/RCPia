"""What a corpus release directory must contain.

Named here rather than in either module that needs it. `ingestion` reads these files and
`manifest` requires a checksum for each, so a release format that grows a file should require
one edit and not two.

A second copy of this list lived in the checksum validator. It was correct the day it was
written, and would have gone stale the first time the format changed — a list standing in for
a rule, which is precisely the finding that removed the source-role domain ceiling.
"""

RELEASE_FILES = ("documents.jsonl", "chunks.jsonl", "chunk.schema.json")
