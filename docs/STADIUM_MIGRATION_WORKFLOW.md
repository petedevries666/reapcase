# Stadium migration workflow

Reapcase treats deployment as a safety-critical, reviewable lifecycle:

```text
STADIUM BACKUP
      ↓
IMPORT
      ↓
*_wip workspace
      ↓
EDIT
      ↓
ANALYZE
      ↓
BUILD
      ↓
VERIFY
      ↓
optional IMPLANT
      ↓
Stadium SD / backups
```

## Import and the source backup

**Import Backup from Stadium** validates every tar member before extraction. Absolute paths,
parent traversal, links, device nodes, and special files are rejected. The source archive is
copied beside a new, uniquely named `_wip` directory and is never edited. Extraction occurs in
a sibling temporary directory and becomes visible only after it succeeds.

The WIP contains Stadium's complete extracted tree plus `.reapcase-workspace.json`. That manifest
records relative package paths where practical. Unknown manifest fields survive updates. Song JSON
lives under `showcase/songs/workspace`, while audio remains independently addressable beneath
`songs/workspace/Audio`; Song IDs and audio directory IDs are not assumed to match.

## Analyze, build, and verify

Build analysis compares Songs semantically and audio by path, size, and SHA-256 when identity must
be confirmed. For user-facing change context, the last package copied by Reapcase is preferred when
it still exists and validates; otherwise the immutable source is used. **The build architecture is
always based on the known-good source archive.** Missing WIP files do not imply deletions.

After explicit review and confirmation, Build safely extracts a fresh source copy into temporary
staging, patches only managed Song JSON and audio, applies exclusions, and creates a new timestamped
archive. Reapcase reopens that archive and validates its paths, Stadium structure, Song JSON,
exclusions, peak policy, and member count before atomically publishing it. A failed verification is
never recorded as `last_built_package`.

Reapcase-only content is centrally denied from Stadium packages, including:

* `*.json.reapcase.json` and `.reapcase-workspace.json`;
* `.reapcase-backups/`;
* `*.reapwave`;
* temporary, debug/performance, platform, and editor-cache files.

Unknown source-backup files, databases, proxy data, screenshots, and configuration remain preserved.

## Peak rebuild policy

Stadium `.peak` files are disposable Stadium-owned caches, not authoritative content. Build removes
all `.peak` files from package staging, and direct Audio Update removes `.peak` files only inside the
unambiguously detected Stadium peak-cache directory. Stadium rebuilds them. Reapcase `.reapwave`
files are different and are never exported.

## Implant

**Implant Stadium Backup** asks for the SD root and requires `backups/`, `clips/`, `screenshots/`,
and `songs/`. It copies the already verified archive only to `backups/`; it never extracts it or
changes the SD's other trees. Existing destinations are refused. A temporary copy is size- and
SHA-256-verified before atomic publication.

`last_built_package` means Reapcase successfully built and verified that local package.
`last_implanted_package` means Reapcase successfully **copied** and verified it on a Stadium SD.
Neither field claims that Stadium restored it, and the UI deliberately does not say “installed.”

## Direct Audio Update

**Update Audio on Stadium SD** is independent of Build/Implant. It recognizes the SD and its audio
and peak roots, maps assets by their actual relative audio paths (including shared assets), skips
SHA-256-identical files, and reviews additions/replacements before applying them. Each changed file
is copied to a temporary sibling, verified, atomically replaced, and verified again. Song differences
against the last copied local package (or source fallback) produce a non-blocking warning because
Audio Update never deploys Song JSON. Peak caches are cleared only after audio copies complete.

## Safety model and limitations

The invariant is **ANALYZE → REVIEW → CONFIRM → APPLY → VERIFY**. Reapcase never silently replaces a
workspace, output archive, backup already on SD, or source archive. V1 has no implicit Song/content
deletion. Cancellation is offered before atomic apply stages; errors report possible partial audio
state rather than claiming success. Reapcase cannot determine whether a copied backup was later
restored on the Stadium, and currently recognizes only the documented unambiguous SD audio layouts.
