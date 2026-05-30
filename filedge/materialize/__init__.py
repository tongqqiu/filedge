"""The Reference Queue Materializer — a first-party example of the external
Queue Materializer role (ADR-0007), the queue mirror of the Reference Fetcher.

Filedge does not consume queues; a Queue Materializer consumes a Queue Source
and lands complete NDJSON Files in a Watched Directory, where `filedge run`
ingests them. This subpackage is a runnable example of that role — external to
the ingestion path, never a loader of record — built on the shared companion
toolkit (`filedge.companion`: Source Manifest emitter, Fetch-Lock promotion,
staging writer).

The pieces:

- ``decoder`` — decode a queue message payload (`bytes`) into a row (`dict`)
- ``config``  — parse a kafka-typed `sources.yaml` entry into a `MaterializePlan`
- ``consumer``— consume a Kafka topic into per-partition Micro-batches
- ``orchestrator`` + ``cli`` — stage, emit manifest, promote, commit offsets
"""
