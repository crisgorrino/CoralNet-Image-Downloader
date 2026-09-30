# CoralNet Image Downloader

A small Python/Tkinter research utility that downloads original-resolution images
from public CoralNet sources and supports local session import for authorized
private sources. It extends the existing v3 GUI and download core.

## Run the application

Use Python 3.9 or newer with Tkinter. The installer from [python.org](https://www.python.org/downloads/)
includes Tkinter on macOS; Homebrew and third-party Python packages are not needed.
`requirements.txt` documents the standard-library-only runtime.

- **macOS:** double-click `CoralNet_Image_Downloader_GUI_v3/Run CoralNet Image Downloader.command`.
  If blocked, right-click and choose Open. Alternatively run the Python command below.
- **Windows:** double-click `CoralNet_Image_Downloader_GUI_v3/Run CoralNet Image Downloader.bat`.
  Install Python with “Add Python to PATH.”
- **Linux:** run the Python command below; your distribution may require `python3-tk`.

```sh
python3 CoralNet_Image_Downloader_GUI_v3/coralnet_image_downloader_gui.py
```

Paste a source URL or numeric ID, choose the output folder (or leave it blank for
a project-named folder in Downloads), choose filters, then click Download Images.
Test mode defaults to the first **10** matching images; the GUI lets you adjust
this count. `TEST_LIMIT` in the core defines the default. Discovery still visits
all matching pages before applying the download limit.

## Public sources

Public sources can be downloaded without login. Leave authentication disconnected.
For example, `8794`, `https://coralnet.ucsd.edu/source/8794/`, and its Browse Images
URL all identify the same source. The downloader extracts the source ID and ignores
pasted query parameters; the GUI controls the annotation filter and pagination.

```sh
python3 download_coralnet_images.py 8794 --test-mode -o ~/Downloads/CoralNet_8794
python3 download_coralnet_images.py 8794 --confirmed-only --limit 10 -o ~/Downloads/CoralNet_8794_confirmed
```

Existing CLI options (`--output`, `--timeout`, `--delay`, `--max-pages`, `--limit`,
`--list-only`) remain available. List-only writes metadata without image URLs or
image downloads. The CLI and GUI now use the same core.

## Private sources

You must already have permission to view the source in CoralNet. Connect first,
then enter the private source's ID or URL and download. If a disconnected request
is denied, the app stops and directs you to Connect to CoralNet; reconnect and
retry the download. Authentication does not grant additional source permissions.

**Validation status:** public download checks and automated access/session tests
are documented in `VALIDATION.md`. Authenticated private-source validation requires
a legitimate account and is pending; do not consider that workflow verified until
it has been exercised with your authorized session.

## Authentication

**The application never asks for or stores your CoralNet password.**

1. Click **Connect to CoralNet**. The app opens CoralNet login in your default browser.
2. Log in normally on CoralNet. The app does not inspect the login form or browser profile.
3. On the CoralNet browser tab, open developer tools. In Chrome/Edge use
   **Application → Storage → Cookies → https://coralnet.ucsd.edu**. In Firefox use
   **Storage → Cookies**. In Safari enable developer features in Settings → Advanced,
   then use **Develop → Show Web Inspector → Storage → Cookies**.
4. Copy only the **Value** of the `sessionid` cookie into the app's masked session
   field, then click **Verify local session**. This is a session secret, not your
   password. Never send it in chat, email, screenshots, or an issue report.
5. The app checks CoralNet's signed-in page before displaying **Connected**.
   Confirmed images only defaults to checked after connecting. You may uncheck it.
6. Clear the clipboard after pasting. Click **Disconnect** or exit to clear the
   app's in-memory session. This does not log your browser out of CoralNet.

The command-line equivalent is `--connect`, which uses a hidden terminal prompt
and defaults to confirmed-only discovery. No secrets are accepted as command-line
arguments. Opening a browser does **not** transfer a session automatically: the
explicit local import is the session-transfer mechanism. This avoids dependencies,
browser extensions, saved cookie exports, and browser keychain/profile access.

The app shows **Not connected**, **Connected**, **Session expired**, or **No access**.
If the session expires, reconnect. If your account lacks access, request access
through the source owner; the downloader stops without attempting a workaround.

## Confirmed images only

This option uses CoralNet's Browse Images filter, not a local definition of
confirmation: `annotation_status=confirmed`. The live form and filtered next-page
link were verified on September 29, 2026 using [source 8794](https://coralnet.ucsd.edu/source/8794/browse/images/?annotation_status=confirmed).
Every pagination request rebuilds this filter. The app verifies the selected
filter on each page and rejects contradictory thumbnail annotation states.
Confirmed runs record `confirmed` in the manifest; unfiltered runs record the
annotation state exposed by CoralNet when available. If an image's status changes
between discovery and detail retrieval, a confirmed-only run marks it failed.

No matching images produces an informational log and an empty manifest with source
metadata. Unexpected HTML/filter changes produce a sanitized error rather than
silently downloading an incomplete dataset.

## Resume and reliability

Re-run with the same source and output folder. Manifest entries are skipped only
when the completed file's byte count and SHA-256 match. Incomplete or corrupted
files restart safely; `.part` files are never considered completed. Existing v3
manifests are migrated. Old CLI files without a recorded checksum are downloaded
again, because completion cannot be verified.

Original names and extensions are preserved where filesystem-safe. Duplicate names
receive an image-ID suffix. Existing untracked files reserve their names and are
not overwritten. A folder containing a manifest for another source is rejected.

Downloads remain sequential, use timeouts and bounded exponential retries, and
pause between requests. Cancel stops discovery, retry waits, hashing, and streaming;
a currently blocked network read may take up to the request timeout to return.
Completed files remain resumable. Files are renamed atomically after validation.
Expired signed image URLs trigger a fresh CoralNet detail request and bounded retry.
No resize, recompression, EXIF, orientation, or format conversion is performed.
Only original-image links are accepted; resized thumbnail-only pages fail.

## Security

- Credentials are not stored; the imported session lives only in app memory.
- Cookies/session data remain local and are sent only to HTTPS `coralnet.ucsd.edu`,
  never its subdomains or external image storage. Cross-origin CoralNet redirects
  and HTTPS downgrades are refused. External storage uses a separate unauthenticated opener.
- Signed URLs are used immediately and never persisted in output files or logs.
  The manifest stores only canonical CoralNet image-detail URLs.
- Transport exceptions are reduced to safe diagnostics. Cookies, authentication
  headers, CSRF tokens, and raw tracebacks are not displayed or logged.
- CoralNet enforces permissions. HTTP 401/403, login redirects/forms, session expiry,
  and access-denied HTML stop the run, including during later pagination/downloads.
- No analytics, telemetry, crash reporting, credential interception, or permission bypass.
- `.gitignore` excludes common local cookie exports, session artifacts, output,
  `.env`, and partial downloads while keeping legitimate session source code visible.

Treat a session cookie like a password: anyone holding it may act as your account.
The explicit import relies on the user selecting the correct CoralNet cookie.
Python clears references on disconnect/exit; it cannot guarantee forensic memory
erasure or clear the browser's clipboard/history. No cookie files are created.
The user's normal browser manages its own existing login persistence.

## Research use

Each output folder includes original image bytes, `download_manifest.csv`,
`SOURCE_INFO.txt`, and `download_errors.txt` when individual downloads fail.

The manifest records source/image IDs, original and local filenames, annotation
state, canonical detail URL, bytes, SHA-256, download status, timestamp, and a safe
error. `SOURCE_INFO.txt` records source URL, filter, authentication mode (no secret),
run timestamp, discovered/selected/completed/failed counts, test limit, run state,
and downloader version. Counts include verified files reused from previous runs.

These artifacts help reproduce research downloads but do **not** create an
immutable snapshot of CoralNet: images, permissions, and annotation states may
change between pages or runs. Preserve your output and manifest with your experiment.
The tool downloads images only; annotation exports and CPCe files are outside scope.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

See `VALIDATION.md` for live regression results and the remaining authenticated
manual checks. No authenticated session data belongs in test fixtures.
