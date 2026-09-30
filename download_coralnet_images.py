#!/usr/bin/env python3
"""Command-line entry point using the desktop app's shared download core."""
from __future__ import annotations

import argparse
import getpass
import sys
import webbrowser
from pathlib import Path
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent / "CoralNet_Image_Downloader_GUI_v3"))
from coralnet_downloader_core import run_download, TEST_LIMIT, DownloadCancelled
from coralnet_session import CoralNetSession, safe_error


def parse_args():
    parser = argparse.ArgumentParser(description="CoralNet Image Downloader")
    parser.add_argument("url", nargs="?", default="6838", help="CoralNet source URL or ID")
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--max-pages", type=int, default=5000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--confirmed-only", action="store_true", default=None)
    parser.add_argument("--connect", action="store_true", help="Open browser and import sessionid locally")
    return parser.parse_args()


def main():
    args = parse_args()
    session = CoralNetSession()
    stop = Event()
    try:
        if args.delay < 0 or args.timeout <= 0 or args.limit < 0 or args.max_pages < 1:
            raise ValueError("Delay/limit must be nonnegative; timeout/page limit must be positive.")
        if args.connect:
            webbrowser.open("https://coralnet.ucsd.edu/accounts/login/")
            print("Log in normally. In browser developer tools → Storage/Application → Cookies,")
            print("copy sessionid for https://coralnet.ucsd.edu. Never share this value.")
            session.connect(getpass.getpass("CoralNet session cookie (hidden): "), stop)
        summary = run_download(
            args.url, args.output, TEST_LIMIT if args.test_mode else args.limit,
            stop, print, print,
            lambda current, total, phase: print(f"{phase}: {current}/{total}"),
            lambda path: print(f"Output: {path}"),
            timeout=args.timeout, delay=args.delay,
            confirmed_only=bool(args.confirmed_only or args.connect), session=session,
            list_only=args.list_only, max_pages=args.max_pages,
        )
        print(f"Discovered: {summary.discovered}; downloaded: {summary.downloaded}; "
              f"previously complete: {summary.previously_complete}; failed: {summary.failed}")
        return 2 if summary.failed else 0
    except (KeyboardInterrupt, DownloadCancelled):
        stop.set()
        print("Stopped. Run again with the same folder to resume.", file=sys.stderr)
        return 130
    except Exception as exc:
        print("ERROR: " + safe_error(exc), file=sys.stderr)
        return 1
    finally:
        session.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
