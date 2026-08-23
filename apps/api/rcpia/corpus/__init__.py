"""Building a corpus release from the reference sources.

The consumer side — loading, validating and retrieving — lives in `rcpia.evidence`. This is the
producer, and the two share a contract rather than code: the builder imports `evidence`'s
validators so a release fails at build time instead of load time, and never its writers, since
conforming to the loader's behaviour would defeat the check.

See `docs/implementation/phase-6-corpus-build.md`.
"""
