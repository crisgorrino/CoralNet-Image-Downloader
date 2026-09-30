#!/usr/bin/env python3
"""Core download logic for the CoralNet Image Downloader desktop app."""

from __future__ import annotations

import csv
import hashlib
import html
import os
import re
import time
from datetime import datetime
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from threading import Event
from typing import Callable, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request

from coralnet_session import CoralNetSession, AccessError, SignedURLExpired, safe_error


VERSION = "4.0.0"
TEST_LIMIT = 10
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
    "local_filename",
    "annotation_status",
    "download_status",
    "bytes",
    "sha256",
    "image_detail_url",
    "downloaded_at",
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
    annotation_states: Tuple[Tuple[str, str], ...] = ()
    confirmed_only: bool = False


@dataclass(frozen=True)
class ImageRecord:
    image_id: str
    detail_url: str
    original_filename: str
    image_url: str = field(repr=False)
    storage_path: str
    annotation_status: str = ""


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
        self.original_srcs: List[str] = []
        self._original_depth = 0
        self._image_id = ""
        self.annotation_states: Dict[str, str] = {}
        self._select = ""
        self.confirmed_selected = False

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
        if tag == "div":
            if values.get("id") == "original_image_container":
                self._original_depth = 1
            elif self._original_depth:
                self._original_depth += 1
        if tag == "select":
            self._select = values.get("name", "")
        if tag == "option" and self._select == "annotation_status":
            if values.get("value") == "confirmed" and "selected" in values:
                self.confirmed_selected = True
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
            match = IMAGE_DETAIL_RE.match(urlsplit(values["href"]).path)
            self._image_id = match.group(1) if match else ""
        elif tag == "img" and values.get("src"):
            self.srcs.append(values["src"])
            if self._original_depth:
                self.original_srcs.append(values["src"])
            states = set(values.get("class", "").split()) & {"confirmed", "unconfirmed", "unclassified"}
            if self._image_id and states:
                self.annotation_states[self._image_id] = states.pop()
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag) -> None:
        if tag.lower() == "div" and self._original_depth:
            self._original_depth -= 1
        if tag.lower() == "a":
            self._image_id = ""
        if tag.lower() == "select":
            self._select = ""
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

    browse_url = build_browse_url(source_id)
    return source_id, browse_url


def build_browse_url(source_id: str, annotation_status: Optional[str] = None,
                     page: Optional[int] = None) -> str:
    if not str(source_id).isdigit() or annotation_status not in {None, "confirmed"}:
        raise ValueError("Invalid source ID or annotation filter.")
    query = {}
    if annotation_status:
        query["annotation_status"] = annotation_status
    if page is not None:
        if page < 1:
            raise ValueError("Page number must be positive.")
        query["page"] = str(page)
    base = f"https://{HOST}/source/{source_id}/browse/images/"
    return base + ("?" + urlencode(query) if query else "")


def _page_url(base_url: str, page_number: int) -> str:
    source_id, _ = normalize_source(base_url)
    query = dict(parse_qsl(urlsplit(base_url).query))
    return build_browse_url(source_id, "confirmed" if query.get("annotation_status") == "confirmed" else None, page_number)


def _request(
    url: str,
    stop_event: Event,
    timeout: float,
    retries: int,
    accept: str,
    session: Optional[CoralNetSession] = None,
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
            with (session or CoralNetSession()).open(request, timeout=timeout) as response:
                headers = {
                    key.lower(): value
                    for key, value in response.headers.items()
                }
                return response.read(), headers, response.geturl()
        except DownloadCancelled:
            raise
        except (HTTPError, URLError, HTTPException, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                for _ in range(min(2 ** (attempt - 1), 8) * 10):
                    _check_cancel(stop_event)
                    time.sleep(0.1)

    raise RuntimeError(f"Unable to fetch page: {safe_error(last_error)}") from None


def _fetch_html(
    url: str,
    stop_event: Event,
    timeout: float,
    retries: int,
    session: Optional[CoralNetSession] = None,
) -> Tuple[str, str]:
    session = session or CoralNetSession()
    body, headers, final_url = _request(
        url,
        stop_event,
        timeout,
        retries,
        "text/html,application/xhtml+xml,*/*;q=0.8",
        session,
    )
    content_type = headers.get("content-type", "")
    charset_match = re.search(
        r"charset=([^\s;]+)", content_type, re.IGNORECASE
    )
    charset = charset_match.group(1) if charset_match else "utf-8"
    document = body.decode(charset, errors="replace")
    session.check_html(document, final_url)
    return document, final_url


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
    if not Path(name).suffix:
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
    confirmed_only: bool = False,
    session: Optional[CoralNetSession] = None,
    max_pages: int = 5000,
) -> SourceInfo:
    source_id, browse_url = normalize_source(source_input)
    session = session or CoralNetSession()
    browse_url = build_browse_url(source_id, "confirmed" if confirmed_only else None)
    status("Connecting to CoralNet…")
    progress(0, 0, "Scanning source")
    log(f"Connecting to source {source_id}: {browse_url}")

    first_html, final_url = _fetch_html(
        browse_url, stop_event, timeout, retries, session
    )

    parser = CoralNetHTMLParser()
    parser.feed(first_html)
    project_name = _project_name_from_title(parser.title, source_id)

    page_match = PAGE_COUNT_RE.search(parser.visible_text)
    total_pages = int(page_match.group(1)) if page_match else 1
    if total_pages > max_pages:
        raise RuntimeError("Source exceeds the browse-page safety limit.")

    count_match = IMAGE_COUNT_RE.search(parser.visible_text)
    reported_count = (
        int(count_match.group(1).replace(",", ""))
        if count_match
        else None
    )

    detail_map: Dict[str, str] = {}
    annotation_states: Dict[str, str] = {}

    def add_page(document: str, current_url: str) -> None:
        page_parser = CoralNetHTMLParser()
        page_parser.feed(document)
        if confirmed_only and not page_parser.confirmed_selected:
            raise RuntimeError("CoralNet did not acknowledge the confirmed filter; discovery stopped.")
        annotation_states.update(page_parser.annotation_states)
        for href in page_parser.hrefs:
            absolute = urljoin(current_url, href)
            parts = urlsplit(absolute)
            match = IMAGE_DETAIL_RE.match(parts.path) if parts.hostname == HOST and parts.scheme == "https" else None
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
            current_url, stop_event, timeout, retries, session
        )
        add_page(document, current_url)
        if delay:
            if stop_event.wait(delay):
                _check_cancel(stop_event)

    if not detail_map and reported_count != 0 and not re.search(r"no images|no results|0 images", parser.visible_text, re.I):
        raise RuntimeError(
            "No image records were found; CoralNet's page layout may have changed."
        )

    if reported_count is not None and len(detail_map) != reported_count:
        raise RuntimeError(
            "CoralNet reported "
            f"{reported_count} images, but {len(detail_map)} unique image "
            "records were discovered; discovery stopped to avoid an incomplete dataset."
        )

    log(f"Unique image records found: {len(detail_map)}")
    if confirmed_only and any(value != "confirmed" for value in annotation_states.values()):
        raise RuntimeError("CoralNet returned an image outside the confirmed filter; discovery stopped.")
    if not detail_map and confirmed_only:
        log("No confirmed images were found in this source.")
    return SourceInfo(
        source_id=source_id,
        browse_url=browse_url,
        project_name=project_name,
        total_pages=total_pages,
        reported_count=reported_count,
        details=tuple(detail_map.items()),
        annotation_states=tuple(annotation_states.items()),
        confirmed_only=confirmed_only,
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
    session: Optional[CoralNetSession] = None,
) -> ImageRecord:
    document, final_url = _fetch_html(
        detail_url, stop_event, timeout, retries, session
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
    if not filename:
        raise RuntimeError("CoralNet image filename is missing; page layout may have changed.")

    candidates: List[str] = []
    for raw_url in parser.original_srcs or (parser.hrefs + parser.srcs):
        absolute = urljoin(detail_url, raw_url)
        parts = urlsplit(absolute)
        if parts.scheme == "https" and "/media/images/" in parts.path.lower() and not RESIZED_IMAGE_RE.search(Path(parts.path).name):
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
        annotation_status=("confirmed" if re.search(r"Annotation status:\s*Confirmed", parser.visible_text, re.I)
                           else "unconfirmed" if re.search(r"Annotation status:\s*Unconfirmed", parser.visible_text, re.I)
                           else "unclassified" if re.search(r"Annotation status:\s*Unclassified", parser.visible_text, re.I) else ""),
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
    session: Optional[CoralNetSession] = None,
    refresh_url: Optional[Callable[[], str]] = None,
) -> Tuple[int, str]:
    session = session or CoralNetSession()
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

            with session.open(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" in content_type:
                    document = response.read(1024 * 1024).decode("utf-8", errors="replace")
                    if urlsplit(response.geturl()).hostname == HOST:
                        session.check_html(document, response.geturl())
                    raise RuntimeError("The image request returned an HTML page.")
                expected_length = response.headers.get("Content-Length")
                with temp_path.open("wb") as output:
                    while True:
                        _check_cancel(stop_event)
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        output.write(block)
                        digest.update(block)
                        byte_count += len(block)

            if expected_length is not None and byte_count != int(expected_length):
                raise RuntimeError("The download was interrupted before completion.")

            if byte_count == 0 or not _file_looks_valid(temp_path):
                raise RuntimeError(
                    "The downloaded response was empty or was not an image."
                )

            temp_path.replace(destination)
            return byte_count, digest.hexdigest()
        except (DownloadCancelled, AccessError):
            try:
                temp_path.unlink()
            except OSError:
                pass
            raise
        except SignedURLExpired:
            temp_path.unlink(missing_ok=True)
            if refresh_url is None or attempt == retries:
                raise RuntimeError("Temporary image URL refresh failed.") from None
            url = refresh_url()
        except (HTTPError, URLError, HTTPException, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
            try:
                temp_path.unlink()
            except OSError:
                pass
            if attempt < retries:
                for _ in range(min(2 ** (attempt - 1), 8) * 10):
                    _check_cancel(stop_event)
                    time.sleep(0.1)

    raise RuntimeError(f"Download failed: {safe_error(last_error)}") from None


def _load_manifest(path: Path) -> Dict[str, Dict[str, str]]:
    rows: Dict[str, Dict[str, str]] = {}
    if not path.exists():
        return rows

    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                image_id = (row.get("image_id") or "").strip()
                if image_id:
                    # Read v3 and the original CLI manifests without retaining URLs.
                    row["local_filename"] = row.get("local_filename") or row.get("saved_filename") or row.get("filename", "")
                    row["download_status"] = row.get("download_status") or row.get("status", "")
                    row["image_detail_url"] = row.get("image_detail_url") or row.get("detail_url", "")
                    row["error"] = safe_error(RuntimeError(row.get("error", ""))) if row.get("error") else ""
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
            # The only persisted URL is a canonical, unsigned CoralNet detail URL.
            row["image_detail_url"] = f"https://{HOST}/image/{image_id}/view/" if image_id.isdigit() else ""
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
    delay: float = 0.25,
    confirmed_only: bool = False,
    session: Optional[CoralNetSession] = None,
    list_only: bool = False,
    max_pages: int = 5000,
) -> DownloadSummary:
    """Discover and download one permitted CoralNet source."""
    session = session or CoralNetSession()
    log_callback = log
    log = lambda message: log_callback(safe_error(RuntimeError(message)))
    source = discover_source(
        source_input=source_input,
        stop_event=stop_event,
        log=log,
        status=status,
        progress=progress,
        timeout=timeout,
        retries=retries,
        delay=delay,
        confirmed_only=confirmed_only,
        session=session,
        max_pages=max_pages,
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
    foreign_sources = {row.get("source_id") for row in manifest.values()} - {None, "", source.source_id}
    if foreign_sources:
        raise RuntimeError("This output folder contains a different source; choose a separate folder.")
    used_names: set[str] = {path.name.casefold() for path in output_dir.iterdir()} | {
        "download_manifest.csv", "download_errors.txt", "source_info.txt",
        "download_manifest.csv.tmp", "source_info.txt.tmp",
    }
    completed_ids: set[str] = set()
    manifest_names: set[str] = set()

    for image_id, row in manifest.items():
        if row.get("source_id") not in {"", source.source_id}:
            continue
        saved_name = row.get("local_filename", "")
        if saved_name:
            if saved_name.casefold() in manifest_names:
                raise RuntimeError("Existing manifest assigns one filename to multiple images; choose a new folder.")
            manifest_names.add(saved_name.casefold())
            used_names.add(saved_name.casefold())
        if saved_name and (Path(saved_name).name != saved_name or saved_name in {".", ".."} or "/" in saved_name or "\\" in saved_name or saved_name.endswith(".part")):
            raise RuntimeError("Unsafe filename in existing manifest.")
        destination = output_dir / saved_name if saved_name else None
        if destination is not None and destination.is_symlink():
            raise RuntimeError("Symlink image destinations are not supported.")
        try:
            expected_size = int(row.get("bytes") or 0)
        except ValueError:
            expected_size = 0

        if (
            row.get("download_status") in {"downloaded", "already_present"}
            and destination is not None
            and _file_looks_valid(destination)
            and (
                expected_size > 0
                and destination.stat().st_size == expected_size
            )
            and bool(row.get("sha256"))
            and _sha256(destination, stop_event) == row["sha256"]
        ):
            completed_ids.add(image_id)

    selected_ids = {image_id for image_id, _ in details}
    completed_ids &= selected_ids
    states = dict(source.annotation_states)
    for image_id in completed_ids:
        manifest[image_id]["annotation_status"] = states.get(image_id, "confirmed" if confirmed_only else "")
    _write_manifest(manifest_path, manifest)

    pending = [
        (image_id, detail_url)
        for image_id, detail_url in details
        if image_id not in completed_ids
    ]

    log(f"Previously completed: {len(completed_ids)}")
    log(f"Remaining this run: {len(pending)}")
    log(f"Output folder: {output_dir}")

    downloaded = 0
    already_present = 0
    failed = 0
    run_state = "in progress"
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    authentication = "authenticated CoralNet session" if session.is_authenticated() else "public access"

    def write_source_info():
        text = (
            "CoralNet Image Downloader\n\n"
            f"Project: {source.project_name}\n"
            f"Source ID: {source.source_id}\n"
            f"Source URL: https://{HOST}/source/{source.source_id}/\n"
            f"Downloaded: {started_at}\n"
            f"Filter: {'annotation_status=confirmed' if confirmed_only else 'none'}\n"
            f"Authentication: {authentication}\n"
            f"Images discovered: {len(source.details)}\n"
            f"Images selected: {len(details)}\n"
            f"Images completed: {len(completed_ids) + downloaded + already_present}\n"
            f"Images failed: {failed}\n"
            f"Test limit: {limit}\n"
            f"Run status: {run_state}\n"
            f"Downloader version: {VERSION}\n\n"
            "Source contents may change; this is not an immutable CoralNet snapshot.\n"
        )
        info_path = output_dir / "SOURCE_INFO.txt"
        temporary = info_path.with_suffix(".txt.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(info_path)

    write_source_info()

    if not pending:
        run_state = "complete"
        write_source_info()
        status(("No confirmed images were found in this source." if confirmed_only else "No images were found in this source.")
               if not details else "All images are already downloaded.")
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
                    session=session,
                )

                if confirmed_only and record.annotation_status and record.annotation_status != "confirmed":
                    raise RuntimeError("Image annotation status changed after discovery; skipped.")

                previous_name = manifest.get(image_id, {}).get(
                    "local_filename", ""
                )
                saved_name = previous_name or _unique_name(
                    _safe_filename(record.original_filename, image_id), image_id, used_names
                )
                destination = output_dir / saved_name

                status(
                    f"Downloading {index} of {len(pending)}: {saved_name}"
                )

                # Only checksum-verified manifest entries are skipped above.
                # Unknown files reserve names; failed/incomplete files restart atomically.
                if list_only:
                    file_size, checksum, row_status = 0, "", "listed"
                else:
                    def refresh_original_url():
                        fresh = _extract_record(image_id, detail_url, stop_event, timeout, retries, session)
                        if confirmed_only and fresh.annotation_status and fresh.annotation_status != "confirmed":
                            raise RuntimeError("Image annotation status changed after discovery; skipped.")
                        return fresh.image_url

                    file_size, checksum = _download_file(
                        record.image_url, destination, stop_event, timeout, retries,
                        session=session,
                        refresh_url=refresh_original_url,
                    )
                    row_status = "downloaded"
                    downloaded += 1

                manifest[image_id] = {
                    "source_id": source.source_id,
                    "image_id": image_id,
                    "original_filename": record.original_filename,
                    "local_filename": saved_name,
                    "download_status": row_status,
                    "bytes": str(file_size),
                    "sha256": checksum,
                    "image_detail_url": record.detail_url,
                    "annotation_status": "confirmed" if confirmed_only else record.annotation_status or dict(source.annotation_states).get(image_id, ""),
                    "downloaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "error": "",
                }
                log(
                    f"[{index}/{len(pending)}] {row_status}: "
                    f"{saved_name} ({_human_size(file_size)})"
                )
            except (DownloadCancelled, AccessError):
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
                    "local_filename": prior.get("local_filename", ""),
                    "download_status": "failed",
                    "bytes": prior.get("bytes", "0"),
                    "sha256": prior.get("sha256", ""),
                    "image_detail_url": detail_url,
                    "annotation_status": dict(source.annotation_states).get(image_id, ""),
                    "downloaded_at": "",
                    "error": safe_error(exc),
                }
                log(
                    f"[{index}/{len(pending)}] ERROR image "
                    f"{image_id}: {safe_error(exc)}"
                )

                with (output_dir / "download_errors.txt").open("a", encoding="utf-8") as errors:
                    errors.write(f"Image {image_id}: {safe_error(exc)}\n")

            _write_manifest(manifest_path, manifest)
            write_source_info()
            progress(index, len(pending), "Downloading")
            if delay:
                if stop_event.wait(delay):
                    _check_cancel(stop_event)

    except AccessError:
        run_state = "stopped: access denied or session expired"
        _write_manifest(manifest_path, manifest)
        write_source_info()
        raise
    except DownloadCancelled:
        run_state = "cancelled"
        write_source_info()
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

    run_state = "finished with errors" if failed else "complete"
    write_source_info()
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
