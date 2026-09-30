# Validation and architecture notes

## Existing architecture inspected

The starting workspace contained only `download_coralnet_images.py`, an older
standard-library CLI. The linked GitHub repository contained the v3 desktop app.
Its source, README, launchers, ignore file, and license were imported from upstream
commit `ec622279279ce1f1207e7fe5a787c148aa70e6b2` before editing. All original
Python files and launchers were read. The local workspace has no `.git` directory;
changes were reviewed with `git diff --no-index` against a saved upstream baseline.
No remote branch, repository name, or deployment was changed.

The GUI entry point is `CoralNet_Image_Downloader_GUI_v3/coralnet_image_downloader_gui.py`.
The existing Tkinter worker/event queue and cancellation event remain in use.
The existing core owns source normalization (`normalize_source`), browse discovery
(`discover_source`, `_page_url`), image-detail parsing (`_extract_record`), streaming
atomic downloads and retries (`_download_file`, `_request`), SHA-256 (`_sha256`),
manifest I/O (`_load_manifest`, `_write_manifest`), collisions (`_unique_name`),
resume and test limits (`run_download`), and callback-based logs/progress.
The GUI previously defaulted test mode to three images; it now defaults to ten.
The old CLI lacked checksums/cancellation controls and stored signed original URLs;
it now delegates to the fuller shared GUI core instead of maintaining a second engine.

## Incremental changes

`coralnet_session.py` adds the shared HTTP opener, exact-host in-memory session
import, verified authentication status, access checks, signed-expiry signaling,
redirect restrictions, and safe diagnostics. External storage uses a separate
unauthenticated opener. No third-party dependencies were added.

`build_browse_url` now owns valid annotation/page parameters. The parser captures
CoralNet's selected filter, thumbnail annotation classes, and original-image
container. Discovery retains the filter through pagination and fails on unexpected
count/layout changes. The original detail/image flow, sequential download loop,
retry backoff, progress callbacks, checksums, and atomic `.part` rename remain.

Resume now requires matching size and checksum. Unknown files reserve names;
ambiguous manifests or unsafe destinations fail rather than overwrite images.
Manifest migration removes storage/original URLs. Output includes updated source
metadata and safe individual-error logs.

## Automated validation

`python3 -m unittest discover -s tests -v`: **21 tests passed**.

Tests cover source parsing, controlled browse URLs, filtered pagination, missing
filter acknowledgment, no matching images, denial/login/expiry detection,
verified local session import, exact-host cookies and redirect boundaries,
401/403 fail-fast behavior, log sanitization, filename collisions, original-only
extraction, expired signed-URL refresh, retries, byte preservation, truncated
responses, cancellation cleanup, checksum resume/corruption, test limits,
untracked-file preservation, safe manifests/metadata, access failure during a
run, and legacy manifest migration. Authentication tests use synthetic cookies
and mocked responses; they do not establish live private-source support.

Compilation passed for the CLI and all desktop modules.
A native macOS Tkinter smoke test created the app and connection dialog, checked
the new controls/defaults, and verified disabled control states while running.
The browser-opening call was mocked; no live cookie was entered in this test.

## Live checks on September 29, 2026

- **Public source 8794:** discovered all 478 images across 24 pages; test batch
  downloaded ten originals with zero failures. All ten files are 2400 × 2400
  pixels (macOS `sips`), matching the original dimensions on the inspected image
  detail page. One downloaded image was opened and visually inspected.
- **Public confirmed-only source 8794:** discovered all 478 matching images across
  24 pages with the filter retained; downloaded ten originals, zero failures.
  Every manifest state is `confirmed`. All ten hashes match the corresponding
  unfiltered originals byte for byte.
- Both batches had their sizes and SHA-256 recomputed independently and matched
  the manifests. No `.part` files remained. Source metadata and manifests were
  checked for session/signed-query markers. Test output lives under `/tmp`, outside
  the source workspace.
- **Live resume:** repeating the public ten-image run verified and skipped all
  ten completed files, downloaded zero additional images, and reported zero failures.
- **Mixed-status public source 4963:** live Browse Images counts were **2,300**
  unfiltered and **900** confirmed-only. The returned form selected Confirmed.
  This comparison fetched only the first browse pages; it did not download the
  entire source.
- **Access-denied source 6838:** CoralNet returned its no-permission HTML; the CLI
  stopped with the authentication/access message before discovery or downloads.
  This was an unauthenticated denial, not a test with an unauthorized signed-in account.

## Required authenticated manual checks — pending

No authorized private account/session was available during implementation.
**Private-source support is implemented but has not been validated end to end.**
The following checks remain before declaring it complete:

1. Launch the GUI and connect using the browser flow described in README. Enter
   the cookie only in the local masked field; never send it in chat or a report.
2. With an account that legitimately has access, use a private source ID, keep
   Confirmed images only checked, enable ten-image test mode, and download.
   Compare the discovered count with CoralNet's filtered Browse Images count.
   Check original dimensions, SHA-256, source metadata, and the safe manifest.
3. Confirm that unfiltered discovery remains available by unchecking the option.
4. Using a signed-in account without access to a specified private source, verify
   No access and immediate termination, with no workaround or image downloads.
5. Invalidate the imported session through CoralNet (for example sign out in the
   browser), then retry. Verify Session expired, no additional downloads, and
   successful reconnection with a newly established session.
6. Disconnect and exit. Verify reconnection is necessary when reopening the app.

A real signed-URL expiry and browser-to-app session transfer were not exercised
live; expiry refresh and import verification are covered by automated tests.
Source contents and annotation status can change during or between runs; the
metadata is a record of a run, not an immutable snapshot.
