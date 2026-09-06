"""The ways media arrives that are not "pick a file" (Phase 6).

A URL pasted into the dialog, a microphone recording, a file dropped into a
watched folder. Every one of them ends at the same door - `media.ingest_path`
or `media.ingest_stream`, then a `transcribe` job - so the library, the dedupe
by content hash and the options model do not have to know which door a
recording came through.

Nothing in this package touches a model, and the one module that reaches the
network (`urls`) is only ever called from a runner child (ADR-001).
"""
