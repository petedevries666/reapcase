# Stadium build I/O architecture

## Measured stages

Set Python logging to `DEBUG` to receive `PERF stadium` records for analysis,
archive writing, and structural verification. Records contain elapsed time,
files, bytes read/written, and audio/non-audio bytes. The UI reports throttled,
byte-weighted progress while files are streamed.

The current pipeline is:

1. inspect the preserved source backup and compare Song JSON;
2. classify audio using the disposable import-provenance manifest (path, size,
   and nanosecond mtime), conservatively treating cache misses as changed;
3. build one merged member plan from the source and authoritative workspace;
4. stream source members or workspace overrides directly to a temporary tar.gz;
5. reopen it for structural/path checks and Song JSON parsing; and
6. atomically rename it to the requested artifact.

There is no extracted build tree and therefore no staging copy of WAV files.
Audio is read once during writing. Verification reads tar/gzip headers and
critical JSON but never hashes or explicitly reads audio payloads.

## Format and trust decisions

A gzip stream is sequential and DEFLATE state crosses tar-member boundaries.
Python `gzip`/`tarfile` cannot safely replace a member or splice arbitrary
compressed ranges. A standards-compatible output therefore still requires a
full tar.gz rewrite. Reapcase deliberately optimizes that rewrite rather than
performing compressed-byte surgery.

The cache records identities immediately after trusted, safe extraction. It is
Reapcase metadata, never an archive member, and is disposable. Size and mtime
avoid multi-GB hashes; a cache miss merely produces conservative `CHANGED`
classification. Archive verification rejects unsafe/duplicate paths, links,
special files, missing Stadium directories, malformed Song JSON, peak caches,
and leaked sidecars. Deep audio hashing is not part of routine builds.

Compression remains Python's compatible default. It was not changed without a
representative multi-GB Stadium corpus and Stadium-device acceptance results.
The debug counters make a level 1/default benchmark possible without conflating
compression gains with the already-removed staging I/O.
