#!/usr/bin/env python3
"""Core download logic for the CoralNet Image Downloader desktop app."""

from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


VERSION = "3.0.0"
HOST = "coralnet.ucsd.edu"
USER_AGENT = f"Mozilla/5.0 (compatible; CoralNetImageDownloader/{VERSION})"

SOURCE_RE = re.compile(r"/source/(\d+)(?:/|$)", re.IGNORECASE)
IMAGE_DETAIL_RE = re.compile(r"^/image/(\d+)/view/?$")
PAGE_COUNT_RE = re.compile(r"Page\s+\d+\s+of\s+(\d+)", re.IGNORECASE)
IMAGE_COUNT_RE = re.compile(
    r"Showing\s+[\d,]+\s*-\s*[\d,]+\s+of\s+([\d,]+)", re.IGNORECASE
)
RESIZED_IMAGE_RE = re.compile(
    r"\.(?:jpe?g|png|tiff?|bmp|gif|webp)"
    r"\.\d+x\d+_q\d+\.(?:jpe?g|png|tiff?|bmp|gif|webp)$",
    re.IGNORECASE,
)
IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"
}
MANIFEST_FIELDS = [
    "source_id",
    "image_id",
    "original_filename",
    "saved_filename",
    "status",
    "bytes",
    "sha256",
    "detail_url",
    "image_storage_path",
    "error",
]

LogCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[int, int, str], None]
OutputCallback = Callable[[Path], None]


class DownloadCancelled(Exception):
    """Raised when the user cancels an active job."""


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    browse_url: str
    project_name: str
    total_pages: int
    reported_count: Optional[int]
    details: Tuple[Tuple[str, str], ...]


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    detail_url: str
    original_filename: str
    image_url: str
    storage_path: str


@dataclass(frozen=True)
class DownloadSummary:
    project_name: str
    source_id: str
    output_dir: Path
    discovered: int
    previously_complete: int
    downloaded: int
    already_present: int
    failed: int
    cancelled: bool = False


class CoralNetHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []
        self.srcs: List[str] = []
        self.text_parts: List[str] = []
        self.title_parts: List[str] = []
        self._in_title = False

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()

    @property
    def visible_text(self) -> str:
        return " ".join(
            part.strip() for part in self.text_parts if part.strip()
        )

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        tag = tag.lower()
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
        elif tag == "img" and values.get("src"):
            self.srcs.append(values["src"])
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data) -> None:
        self.text_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)


def _check_cancel(stop_event: Event) -> None:
    if stop_event.is_set():
        raise DownloadCancelled("Download cancelled by the user.")


def normalize_source(value: str) -> Tuple[str, str]:
    """Accept a source ID or any URL containing /source/NUMBER/."""
    value = value.strip()
    if not value:
        raise ValueError("Enter a CoralNet source URL or source ID.")

    if value.isdigit():
        source_id = value
    else:
        if "://" not in value:
            value = "https://" + value.lstrip("/")
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"}:
            raise ValueError("The URL must begin with http:// or https://.")
        if parts.netloc.lower() != HOST:
            raise ValueError(f"The URL must be hosted on {HOST}.")
        match = SOURCE_RE.search(parts.path)
        if not match:
            raise ValueError(
                "The URL must contain a CoralNet source path such as "
                "/source/6838/."
            )
        source_id = match.group(1)

    browse_url = f"https://{HOST}/source/{source_id}/browse/images/"
    return source_id, browse_url


def _page_url(base_url: str, page_number: int) -> str:
    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["page"] = str(page_number)
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _request(
    url: str,
    stop_event: Event,
    timeout: float,
    retries: int,
    accept: str,
) -> Tuple[bytes, Dict[str, str], str]:
    last_error: Optional[BaseException] = None

    for attempt in range(1, retries + 1):
        _check_cancel(stop_event)
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": accept,
                    "Accept-Encoding": "identity",
                },
            )
            with urlopen(request, timeout=timeout) as response:
                headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                }
                return response.read(), headers, response.geturl()
        except DownloadCancelled:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                for _ in range(min(2 ** (attempt - 1), 8) * 10):
                    _check_cancel(stop_event)
                    time.sleep(0.1)

    raise RuntimeError(f"Unable to fetch {url}: {last_error}")


def _fetch_html(
    url: str,
    stop_event: Event,
    timeout: float,
    retries: int,
) -> Tuple[str, str]:
    body, headers, final_url = _request(
        url,
        stop_event,
        timeout,
        retries,
        "text/html,application/xhtml+xml,*/*;q=0.8",
    )
    content_type = headers.get("content-type", "")
    charset_match = re.search(
        r"charset=([^\s;]+)", content_type, re.IGNORECASE
    )
    charset = charset_match.group(1) if charset_match else "utf-8"
    return body.decode(charset, errors="replace"), final_url


def _safe_folder_name(name: str) -> str:
    name = html.unescape(name).strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or "CoralNet Images"


def _safe_filename(name: str, image_id: str) -> str:
    name = html.unescape(name).strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r"[\x00-\x1f\x7f]", "_", name).strip(" .")

    if not name:
        name = f"coralnet_{image_id}.jpg"
    if Path(name).suffix.lower() not in IMAGE_EXTENSIONS:
        name += ".jpg"
    return name


def _project_name_from_title(title: str, source_id: str) -> str:
    suffixes = (
        " - Browse Images | CoralNet",
        " – Browse Images | CoralNet",
        " | CoralNet",
    )
    for suffix in suffixes:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break
    return title or f"CoralNet source {source_id}"


def discover_source(
    source_input: str,
    stop_event: Event,
    log: LogCallback,
    status: StatusCallback,
    progress: ProgressCallback,
    timeout: float,
    retries: int,
    delay: float,
) -> SourceInfo:
    source_id, browse_url = normalize_source(source_input)
    status("Connecting to CoralNet…")
    progress(0, 0, "Scanning source")
    log(f"Connecting to source {source_id}: {browse_url}")

    first_html, final_url = _fetch_html(
        browse_url, stop_event, timeout, retries
    )
    if "/accounts/login" in urlsplit(final_url).path.lower():
        raise RuntimeError(
            "This source requires a CoralNet login. The desktop downloader "
            "currently supports public sources only."
        )

    parser = CoralNetHTMLParser()
    parser.feed(first_html)
    project_name = _project_name_from_title(parser.title, source_id)

    page_match = PAGE_COUNT_RE.search(parser.visible_text)
    total_pages = int(page_match.group(1)) if page_match else 1

    count_match = IMAGE_COUNT_RE.search(parser.visible_text)
    reported_count = (
        int(count_match.group(1).replace(",", ""))
        if count_match
        else None
    )

    detail_map: Dict[str, str] = {}

    def add_page(document: str, current_url: str) -> None:
        page_parser = CoralNetHTMLParser()
        page_parser.feed(document)
        for href in page_parser.hrefs:
            absolute = urljoin(current_url, href)
            match = IMAGE_DETAIL_RE.match(urlsplit(absolute).path)
            if match:
                detail_map.setdefault(match.group(1), absolute)

    add_page(first_html, browse_url)
    log(f"Project: {project_name}")
    log(f"Browse pages: {total_pages}")
    if reported_count is not None:
        log(f"Images reported by CoralNet: {reported_count}")

    for page_number in range(2, total_pages + 1):
        _check_cancel(stop_event)
        status(f"Scanning browse page {page_number} of {total_pages}…")
        progress(page_number - 1, total_pages, "Scanning source")
        current_url = _page_url(browse_url, page_number)
        document, _ = _fetch_html(
            current_url, stop_event, timeout, retries
        )
        add_page(document, current_url)
        if delay:
            time.sleep(delay)

    if not detail_map and reported_count != 0:
        raise RuntimeError(
            "No image records were found. The source may be private, empty, "
            "or CoralNet's page layout may have changed."
        )

    if reported_count is not None and len(detail_map) != reported_count:
        log(
            "Warning: CoralNet reported "
            f"{reported_count} images, but {len(detail_map)} unique image "
            "records were discovered."
        )

    log(f"Unique image records found: {len(detail_map)}")
    return SourceInfo(
        source_id=source_id,
        browse_url=browse_url,
        project_name=project_name,
        total_pages=total_pages,
        reported_count=reported_count,
        details=tuple(detail_map.items()),
    )


def _candidate_score(url: str) -> Tuple[int, int, int, int]:
    path = urlsplit(url).path
    basename = Path(path).name
    return (
        1 if "/media/images/" in path.lower() else 0,
        0 if RESIZED_IMAGE_RE.search(basename) else 1,
        1 if Path(basename).suffix.lower() in IMAGE_EXTENSIONS else 0,
        -len(path),
    )


def _extract_record(
    image_id: str,
    detail_url: str,
    stop_event: Event,
    timeout: float,
    retries: int,
) -> ImageRecord:
    document, final_url = _fetch_html(
        detail_url, stop_event, timeout, retries
    )
    if "/accounts/login" in urlsplit(final_url).path.lower():
        raise RuntimeError("Login required for this image.")

    parser = CoralNetHTMLParser()
    parser.feed(document)

    filename = (
        parser.title.split(" | CoralNet", 1)[0].strip()
        if parser.title
        else ""
    )
    filename = _safe_filename(filename, image_id)

    candidates: List[str] = []
    for raw_url in parser.hrefs + parser.srcs:
        absolute = urljoin(detail_url, raw_url)
        if "/media/images/" in urlsplit(absolute).path.lower():
            candidates.append(absolute)

    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        raise RuntimeError("No original-resolution image link was found.")

    image_url = max(candidates, key=_candidate_score)
    parts = urlsplit(image_url)
    storage_path = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "", "")
    )

    return ImageRecord(
        image_id=image_id,
        detail_url=detail_url,
        original_filename=filename,
        image_url=image_url,
        storage_path=storage_path,
    )


def _file_looks_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with path.open("rb") as handle:
            head = handle.read(64).lstrip().lower()
    except OSError:
        return False
    return not (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or head.startswith(b"<?xml")
    )


def _sha256(path: Path, stop_event: Event) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            _check_cancel(stop_event)
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _download_file(
    url: str,
    destination: Path,
    stop_event: Event,
    timeout: float,
    retries: int,
) -> Tuple[int, str]:
    temp_path = destination.with_name(destination.name + ".part")
    last_error: Optional[BaseException] = None

    for attempt in range(1, retries + 1):
        _check_cancel(stop_event)
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "image/*,*/*;q=0.8",
                    "Accept-Encoding": "identity",
                },
            )
            digest = hashlib.sha256()
            byte_count = 0

            with urlopen(request, timeout=timeout) as response:
                with temp_path.open("wb") as output:
                    while True:
                        _check_cancel(stop_event)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        digest.update(block)
                        byte_count += len(block)

            if byte_count == 0 or not _file_looks_valid(temp_path):
                raise RuntimeError(
                    "The downloaded response was empty or was not an image."
                )

            temp_path.replace(destination)
            return byte_count, digest.hexdigest()
        except DownloadCancelled:
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            try:
                temp_path.unlink()
            except OSError:
                pass
            if attempt < retries:
                for _ in range(min(2 ** (attempt - 1), 8) * 10):
                    _check_cancel(stop_event)
                    time.sleep(0.1)

    raise RuntimeError(f"Download failed: {last_error}")


def _load_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    if not path.exists():
        return rows

    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                image_id = (row.get("image_id") or "").strip()
                if image_id:
                    rows[image_id] = row
    except (OSError, csv.Error) as exc:
        raise RuntimeError(f"Could not read the existing manifest: {exc}")
    return rows


def _write_manifest(
    path: Path, rows: Dict[str, Dict[str, str]]
) -> None:
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for image_id in sorted(
            rows, key=lambda value: int(value) if value.isdigit() else value
        ):
            row = {
                field: rows[image_id].get(field, "")
                for field in MANIFEST_FIELDS
            }
            writer.writerow(row)
    temp_path.replace(path)


def _unique_name(
    filename: str, image_id: str, used_names: set[str]
) -> str:
    candidate = filename
    key = candidate.casefold()

    if key in used_names:
        path = Path(candidate)
        candidate = (
            f"{path.stem}__coralnet-{image_id}{path.suffix}"
        )
        key = candidate.casefold()

    counter = 2
    while key in used_names:
        path = Path(candidate)
        candidate = f"{path.stem}_{counter}{path.suffix}"
        key = candidate.casefold()
        counter += 1

    used_names.add(key)
    return candidate


def _default_output_dir(source: SourceInfo) -> Path:
    downloads = Path.home() / "Downloads"
    parent = downloads if downloads.exists() else Path.home()
    folder = _safe_folder_name(
        f"{source.project_name} - source {source.source_id}"
    )
    return (parent / folder).resolve()


def run_download(
    source_input: str,
    requested_output: Optional[Path],
    limit: int,
    stop_event: Event,
    log: LogCallback,
    status: StatusCallback,
    progress: ProgressCallback,
    output_ready: OutputCallback,
    timeout: float = 90.0,
    retries: int = 4,
    delay: float = 0.15,
) -> DownloadSummary:
    """Discover and download one public CoralNet source."""
    source = discover_source(
        source_input=source_input,
        stop_event=stop_event,
        log=log,
        status=status,
        progress=progress,
        timeout=timeout,
        retries=retries,
        delay=delay,
    )
    _check_cancel(stop_event)

    details = list(source.details)
    if limit > 0:
        details = details[:limit]
        log(f"Test limit enabled: processing the first {len(details)} images.")

    output_dir = (
        requested_output.expanduser().resolve()
        if requested_output
        else _default_output_dir(source)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output_ready(output_dir)

    manifest_path = output_dir / "download_manifest.csv"
    manifest = _load_manifest(manifest_path)
    used_names: set[str] = set()
    completed_ids: set[str] = set()

    for image_id, row in manifest.items():
        if row.get("source_id") not in {"", source.source_id}:
            continue
        saved_name = row.get("saved_filename", "")
        if saved_name:
            used_names.add(saved_name.casefold())
        destination = output_dir / saved_name if saved_name else None
        try:
            expected_size = int(row.get("bytes") or 0)
        except ValueError:
            expected_size = 0

        if (
            row.get("status") in {"downloaded", "already_present"}
            and destination is not None
            and _file_looks_valid(destination)
            and (
                expected_size == 0
                or destination.stat().st_size == expected_size
            )
        ):
            completed_ids.add(image_id)

    pending = [
        (image_id, detail_url)
        for image_id, detail_url in details
        if image_id not in completed_ids
    ]

    log(f"Previously completed: {len(completed_ids)}")
    log(f"Remaining this run: {len(pending)}")
    log(f"Output folder: {output_dir}")

    source_info = (
        "CoralNet image download\n"
        "========================\n\n"
        f"Project: {source.project_name}\n"
        f"Source ID: {source.source_id}\n"
        f"Browse URL: {source.browse_url}\n"
        f"Images discovered for this run: {len(details)}\n"
        f"Downloader version: {VERSION}\n\n"
        "The CSV manifest intentionally excludes temporary signed URL "
        "query credentials.\n"
    )
    (output_dir / "SOURCE_INFO.txt").write_text(
        source_info, encoding="utf-8"
    )

    downloaded = 0
    already_present = 0
    failed = 0

    if not pending:
        status("All images are already downloaded.")
        progress(len(details), len(details), "Complete")
        return DownloadSummary(
            project_name=source.project_name,
            source_id=source.source_id,
            output_dir=output_dir,
            discovered=len(details),
            previously_complete=len(completed_ids),
            downloaded=0,
            already_present=0,
            failed=0,
        )

    status("Downloading original-resolution images…")
    progress(0, len(pending), "Downloading")

    try:
        for index, (image_id, detail_url) in enumerate(pending, start=1):
            _check_cancel(stop_event)
            status(
                f"Preparing image {index} of {len(pending)}…"
            )

            try:
                record = _extract_record(
                    image_id=image_id,
                    detail_url=detail_url,
                    stop_event=stop_event,
                    timeout=timeout,
                    retries=retries,
                )

                previous_name = manifest.get(image_id, {}).get(
                    "saved_filename", ""
                )
                saved_name = previous_name or _unique_name(
                    record.original_filename, image_id, used_names
                )
                destination = output_dir / saved_name

                status(
                    f"Downloading {index} of {len(pending)}: {saved_name}"
                )

                if _file_looks_valid(destination):
                    file_size = destination.stat().st_size
                    checksum = _sha256(destination, stop_event)
                    row_status = "already_present"
                    already_present += 1
                else:
                    file_size, checksum = _download_file(
                        record.image_url,
                        destination,
                        stop_event,
                        timeout,
                        retries,
                    )
                    row_status = "downloaded"
                    downloaded += 1

                manifest[image_id] = {
                    "source_id": source.source_id,
                    "image_id": image_id,
                    "original_filename": record.original_filename,
                    "saved_filename": saved_name,
                    "status": row_status,
                    "bytes": str(file_size),
                    "sha256": checksum,
                    "detail_url": record.detail_url,
                    "image_storage_path": record.storage_path,
                    "error": "",
                }
                log(
                    f"[{index}/{len(pending)}] {row_status}: "
                    f"{saved_name} ({_human_size(file_size)})"
                )
            except DownloadCancelled:
                raise
            except Exception as exc:
                failed += 1
                prior = manifest.get(image_id, {})
                manifest[image_id] = {
                    "source_id": source.source_id,
                    "image_id": image_id,
                    "original_filename": prior.get(
                        "original_filename", ""
                    ),
                    "saved_filename": prior.get("saved_filename", ""),
                    "status": "failed",
                    "bytes": prior.get("bytes", "0"),
                    "sha256": prior.get("sha256", ""),
                    "detail_url": detail_url,
                    "image_storage_path": prior.get(
                        "image_storage_path", ""
                    ),
                    "error": str(exc),
                }
                log(
                    f"[{index}/{len(pending)}] ERROR image "
                    f"{image_id}: {exc}"
                )

            _write_manifest(manifest_path, manifest)
            progress(index, len(pending), "Downloading")
            if delay:
                time.sleep(delay)

    except DownloadCancelled:
        _write_manifest(manifest_path, manifest)
        status("Download cancelled. Run again to resume.")
        return DownloadSummary(
            project_name=source.project_name,
            source_id=source.source_id,
            output_dir=output_dir,
            discovered=len(details),
            previously_complete=len(completed_ids),
            downloaded=downloaded,
            already_present=already_present,
            failed=failed,
            cancelled=True,
        )

    status(
        "Finished with errors."
        if failed
        else "Download complete."
    )
    progress(len(pending), len(pending), "Complete")
    return DownloadSummary(
        project_name=source.project_name,
        source_id=source.source_id,
        output_dir=output_dir,
        discovered=len(details),
        previously_complete=len(completed_ids),
        downloaded=downloaded,
        already_present=already_present,
        failed=failed,
    )


def _human_size(number: int) -> str:
    value = float(number)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{number} B"
