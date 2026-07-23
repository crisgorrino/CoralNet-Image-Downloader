# CoralNet Image Downloader — Desktop UI

A small cross-platform desktop app for downloading all original-resolution
images from a **public CoralNet source**.

## What the app does

- Accepts a CoralNet source ID or source URL
- Finds every page in the source's Browse Images section
- Downloads original-resolution images instead of thumbnails
- Preserves original filenames whenever possible
- Adds a CoralNet image-ID suffix when duplicate filenames exist
- Resumes interrupted downloads using `download_manifest.csv`
- Retries failed requests
- Calculates SHA-256 checksums
- Provides progress, activity logs, cancellation, and an Open Folder button
- Does not store CoralNet's temporary signed URL credentials

## macOS

1. Extract the ZIP file.
2. Double-click **Run CoralNet Image Downloader.command**.
3. Paste the CoralNet source URL.
4. Leave Output folder blank for the automatic Downloads location, or click
   **Browse…**.
5. Click **Download Images**.

If macOS blocks the launcher:

1. Right-click the `.command` file.
2. Choose **Open**.
3. Confirm **Open** again.

Python 3 with Tkinter is required. The standard installer from python.org
normally includes Tkinter.

## Windows

1. Extract the ZIP file.
2. Double-click **Run CoralNet Image Downloader.bat**.
3. Paste the CoralNet source URL.
4. Choose an output folder or leave it blank.
5. Click **Download Images**.

Install Python 3 from python.org and enable **Add Python to PATH** during setup.

## Linux

Run:

```bash
python3 coralnet_image_downloader_gui.py
```

Tkinter may need to be installed separately, for example `python3-tk` on
Debian/Ubuntu.

## Accepted CoralNet input

All of these work:

```text
6838
https://coralnet.ucsd.edu/source/6838/
https://coralnet.ucsd.edu/source/6838/browse/images/
https://coralnet.ucsd.edu/source/6838/browse/images/?page=4
```

The app normalizes the input to the complete Browse Images directory. Any page
number or filter in the pasted URL is intentionally ignored so that the full
source is downloaded.

## Test mode

Enable **Test mode** to download only the first few images before running the
entire source.

## Resume behavior

Run the app again with the same output folder. It checks the existing manifest
and local files, skips valid completed images, and retries incomplete or failed
ones.

## Output files

The selected output folder contains:

- Original-resolution image files
- `download_manifest.csv`
- `SOURCE_INFO.txt`
- A temporary `.part` file only while one image is actively downloading

## Current limitations

- Public CoralNet sources only
- Images only; CPCe files and annotation exports are not downloaded
- Requires CoralNet's current public HTML page structure
- A native signed `.app` or `.exe` is not included; the launchers use Python 3
