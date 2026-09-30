#!/usr/bin/env python3
"""Desktop user interface for the CoralNet Image Downloader."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as exc:
    raise SystemExit(
        "Tkinter is not available in this Python installation. "
        "Install Python 3 from python.org and try again."
    ) from exc

from coralnet_downloader_core import DownloadSummary, VERSION, TEST_LIMIT, run_download
from coralnet_session import CoralNetSession, safe_error


class CoralNetDownloaderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"CoralNet Image Downloader {VERSION}")
        self.geometry("880x680")
        self.minsize(720, 560)

        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.current_output: Optional[Path] = None
        self.session = CoralNetSession()
        self.auth_var = tk.StringVar(value="Not connected")
        self.confirmed_var = tk.BooleanVar(value=False)

        self.url_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.test_mode_var = tk.BooleanVar(value=False)
        self.limit_var = tk.IntVar(value=TEST_LIMIT)
        self.open_when_done_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")
        self.progress_text_var = tk.StringVar(value="")
        self.stats_var = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._process_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(
            outer,
            text="CoralNet Image Downloader",
            font=("TkDefaultFont", 18, "bold"),
        )
        title.pack(anchor="w")

        subtitle = ttk.Label(
            outer,
            text=(
                "Download original-resolution images from public or authorized private CoralNet sources."
            ),
        )
        subtitle.pack(anchor="w", pady=(2, 16))

        form = ttk.Frame(outer)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="CoralNet source URL or ID").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=5
        )
        self.url_entry = ttk.Entry(form, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.url_entry.insert(
            0,
            "https://coralnet.ucsd.edu/source/6838/browse/images/",
        )

        ttk.Label(form, text="Output folder").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=5
        )
        output_frame = ttk.Frame(form)
        output_frame.grid(row=1, column=1, sticky="ew", pady=5)
        output_frame.columnconfigure(0, weight=1)

        self.output_entry = ttk.Entry(
            output_frame, textvariable=self.output_var
        )
        self.output_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(
            output_frame,
            text="Browse…",
            command=self._browse_output,
        ).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(
            form,
            text=(
                "Leave blank to create a project-named folder in Downloads."
            ),
        ).grid(row=2, column=1, sticky="w", pady=(0, 8))

        options = ttk.LabelFrame(outer, text="Options", padding=10)
        options.pack(fill="x", pady=(10, 12))

        self.connect_button = ttk.Button(options, text="Connect to CoralNet", command=self._connect)
        self.connect_button.grid(row=0, column=0, sticky="w")
        ttk.Label(options, textvariable=self.auth_var).grid(row=0, column=1, columnspan=2, sticky="w", padx=10)
        self.disconnect_button = ttk.Button(options, text="Disconnect", command=self._disconnect)
        self.disconnect_button.grid(row=0, column=3, padx=8)
        self.confirmed_checkbox = ttk.Checkbutton(options, text="Confirmed images only", variable=self.confirmed_var)
        self.confirmed_checkbox.grid(row=1, column=0, columnspan=4, sticky="w", pady=8)

        self.test_checkbox = ttk.Checkbutton(
            options,
            text="Test mode — download only the first",
            variable=self.test_mode_var,
            command=self._toggle_test_mode,
        )
        self.test_checkbox.grid(row=2, column=0, sticky="w")

        self.limit_spinbox = ttk.Spinbox(
            options,
            from_=1,
            to=10000,
            width=7,
            textvariable=self.limit_var,
            state="disabled",
        )
        self.limit_spinbox.grid(row=2, column=1, padx=(6, 4))
        ttk.Label(options, text="images").grid(row=2, column=2, sticky="w")

        ttk.Checkbutton(
            options,
            text="Open the output folder when finished",
            variable=self.open_when_done_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(0, 12))

        self.start_button = ttk.Button(
            actions,
            text="Download Images",
            command=self._start_download,
        )
        self.start_button.pack(side="left")

        self.cancel_button = ttk.Button(
            actions,
            text="Cancel",
            command=self._cancel_download,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=(8, 0))

        self.open_button = ttk.Button(
            actions,
            text="Open Output Folder",
            command=self._open_output,
            state="disabled",
        )
        self.open_button.pack(side="right")

        progress_frame = ttk.Frame(outer)
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(
            progress_frame,
            mode="determinate",
            maximum=100,
        )
        self.progress.pack(fill="x")

        progress_labels = ttk.Frame(progress_frame)
        progress_labels.pack(fill="x", pady=(4, 0))
        ttk.Label(
            progress_labels, textvariable=self.status_var
        ).pack(side="left")
        ttk.Label(
            progress_labels, textvariable=self.progress_text_var
        ).pack(side="right")

        ttk.Label(
            outer,
            textvariable=self.stats_var,
        ).pack(anchor="w", pady=(8, 4))

        log_frame = ttk.LabelFrame(outer, text="Activity log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(
            log_frame,
            height=16,
            wrap="word",
            state="disabled",
            font=("TkFixedFont", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient="vertical",
            command=self.log_text.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.url_entry.focus_set()
        self.url_entry.selection_range(0, "end")

    def _toggle_test_mode(self) -> None:
        self.limit_spinbox.configure(
            state="normal" if self.test_mode_var.get() else "disabled"
        )

    def _disconnect(self) -> None:
        self.session.disconnect()
        self.auth_var.set(self.session.state)

    def _connect(self) -> None:
        webbrowser.open("https://coralnet.ucsd.edu/accounts/login/")
        dialog = tk.Toplevel(self)
        dialog.title("Connect to CoralNet")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=(
            "Log in to CoralNet in your browser normally.\n"
            "In browser developer tools, open Application/Storage → Cookies\n"
            "for https://coralnet.ucsd.edu and copy the sessionid value.\n"
            "Safari: Web Inspector → Storage → Cookies.\n\n"
            "Paste only that value below. It stays in memory until you disconnect\n"
            "or exit. Never send it in chat, email, or screenshots.\n"
            "The application never asks for or stores your CoralNet password."
        ), justify="left").pack(anchor="w")
        entry = ttk.Entry(frame, show="•", width=55)
        entry.pack(fill="x", pady=12)

        def dismiss():
            entry.delete(0, "end")
            dialog.destroy()

        def verify():
            cookie = entry.get()
            dismiss()
            self.stop_event.clear()
            self.auth_var.set("Connecting…")
            self._set_running(True)
            self.worker = threading.Thread(target=self._connect_worker, args=(cookie,), daemon=True)
            self.worker.start()

        ttk.Button(frame, text="Verify local session", command=verify).pack(side="left")
        ttk.Button(frame, text="Cancel", command=dismiss).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dismiss)
        entry.focus_set()

    def _connect_worker(self, cookie: str) -> None:
        try:
            self.session.connect(cookie, self.stop_event)
            self.events.put(("connected",))
        except Exception as exc:
            self.events.put(("error", safe_error(exc), ""))

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(
            title="Choose an output folder",
            initialdir=(
                self.output_var.get()
                or str(Path.home() / "Downloads")
            ),
        )
        if selected:
            self.output_var.set(selected)

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", safe_error(RuntimeError(message)).rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        for control in (self.connect_button, self.disconnect_button, self.confirmed_checkbox, self.limit_spinbox):
            control.configure(state="disabled" if running else "normal")
        self.start_button.configure(
            state="disabled" if running else "normal"
        )
        self.cancel_button.configure(
            state="normal" if running else "disabled"
        )
        self.url_entry.configure(
            state="disabled" if running else "normal"
        )
        self.output_entry.configure(
            state="disabled" if running else "normal"
        )
        self.test_checkbox.configure(
            state="disabled" if running else "normal"
        )
        if not running:
            self._toggle_test_mode()

    def _start_download(self) -> None:
        source = self.url_var.get().strip()
        if not source:
            messagebox.showerror(
                "Missing CoralNet URL",
                "Paste a CoralNet source URL or enter its numeric source ID.",
            )
            return

        requested_output: Optional[Path] = None
        if self.output_var.get().strip():
            requested_output = Path(self.output_var.get().strip())

        try:
            limit = self.limit_var.get() if self.test_mode_var.get() else 0
        except tk.TclError:
            messagebox.showerror("Invalid test limit", "Enter a positive integer for the test limit.")
            return
        if limit < 1 and self.test_mode_var.get():
            messagebox.showerror(
                "Invalid test limit",
                "The test limit must be at least 1.",
            )
            return

        self.stop_event.clear()
        self.current_output = None
        self.open_button.configure(state="disabled")
        self.stats_var.set("")
        self.progress_text_var.set("")
        self.status_var.set("Starting…")
        self.progress.stop()
        self.progress.configure(mode="indeterminate", maximum=100)
        self.progress.start(10)
        self._append_log("")
        self._append_log("=" * 64)
        self._append_log("Starting a new CoralNet download")
        self._set_running(True)

        self.worker = threading.Thread(
            target=self._worker_main,
            args=(source, requested_output, limit, self.confirmed_var.get()),
            daemon=True,
        )
        self.worker.start()

    def _worker_main(
        self,
        source: str,
        requested_output: Optional[Path],
        limit: int,
        confirmed_only: bool,
    ) -> None:
        try:
            summary = run_download(
                source_input=source,
                requested_output=requested_output,
                limit=limit,
                confirmed_only=confirmed_only,
                session=self.session,
                stop_event=self.stop_event,
                log=lambda message: self.events.put(("log", message)),
                status=lambda message: self.events.put(
                    ("status", message)
                ),
                progress=lambda current, total, phase: self.events.put(
                    ("progress", current, total, phase)
                ),
                output_ready=lambda path: self.events.put(
                    ("output", str(path))
                ),
            )
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(
                (
                    "error",
                    safe_error(exc),
                    "",
                )
            )

    def _cancel_download(self) -> None:
        if self.worker and self.worker.is_alive():
            self.stop_event.set()
            self.status_var.set("Cancelling…")
            self.cancel_button.configure(state="disabled")
            self._append_log(
                "Cancellation requested. The current request will stop safely."
            )

    def _process_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]

                if kind == "log":
                    self._append_log(event[1])
                elif kind == "status":
                    self.status_var.set(event[1])
                elif kind == "progress":
                    self._update_progress(
                        event[1], event[2], event[3]
                    )
                elif kind == "output":
                    self.current_output = Path(event[1])
                    self.output_var.set(event[1])
                    self.open_button.configure(state="normal")
                elif kind == "done":
                    self._finish(event[1])
                elif kind == "error":
                    self._fail(event[1], event[2])
                elif kind == "connected":
                    self._set_running(False)
                    self.auth_var.set(self.session.state)
                    self.confirmed_var.set(True)
                    self.status_var.set("Connected to CoralNet")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_events)

    def _update_progress(
        self, current: int, total: int, phase: str
    ) -> None:
        if total <= 0:
            if str(self.progress.cget("mode")) != "indeterminate":
                self.progress.configure(mode="indeterminate")
                self.progress.start(10)
            self.progress_text_var.set(phase)
            return

        self.progress.stop()
        self.progress.configure(mode="determinate", maximum=total)
        self.progress["value"] = min(current, total)
        self.progress_text_var.set(
            f"{phase}: {current} / {total}"
        )

    def _finish(self, summary: DownloadSummary) -> None:
        self.auth_var.set(self.session.state)
        self.progress.stop()
        self._set_running(False)
        self.current_output = summary.output_dir
        self.open_button.configure(state="normal")

        self.stats_var.set(
            f"Found: {summary.discovered}    "
            f"Previously complete: {summary.previously_complete}    "
            f"Downloaded: {summary.downloaded}    "
            f"Already present: {summary.already_present}    "
            f"Failed: {summary.failed}"
        )

        if summary.discovered == 0:
            message = "No confirmed images were found in this source." if self.confirmed_var.get() else "No images were found in this source."
            self.status_var.set(message)
            self._append_log(message)
            messagebox.showinfo("No matching images", message)
            return

        if summary.cancelled:
            self.status_var.set(
                "Cancelled — run again with the same folder to resume."
            )
            self._append_log(
                "Cancelled safely. Completed files and the manifest were kept."
            )
            messagebox.showinfo(
                "Download cancelled",
                "The download was cancelled safely.\n\n"
                "Run it again with the same output folder to resume.",
            )
            return

        self.status_var.set(
            "Finished with errors."
            if summary.failed
            else "Download complete."
        )
        self._append_log(
            f"Finished. Output folder: {summary.output_dir}"
        )

        if self.open_when_done_var.get():
            self._open_output()

        if summary.failed:
            messagebox.showwarning(
                "Finished with errors",
                f"{summary.failed} image(s) failed.\n\n"
                "Run the downloader again with the same output folder "
                "to retry them.",
            )
        else:
            messagebox.showinfo(
                "Download complete",
                f"Downloaded images are in:\n{summary.output_dir}",
            )

    def _fail(self, message: str, details: str) -> None:
        self.auth_var.set(self.session.state)
        self.progress.stop()
        self._set_running(False)
        self.status_var.set("Error")
        self.progress_text_var.set("")
        self._append_log(f"ERROR: {message}")
        messagebox.showerror("CoralNet download error", message)

    def _open_output(self) -> None:
        path = self.current_output
        if path is None and self.output_var.get().strip():
            path = Path(self.output_var.get().strip())

        if path is None or not path.exists():
            messagebox.showinfo(
                "Output folder",
                "The output folder has not been created yet.",
            )
            return

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror(
                "Could not open folder", str(exc)
            )

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            close = messagebox.askyesno(
                "Download in progress",
                "Cancel the download and close the app?",
            )
            if not close:
                return
            self.stop_event.set()
        self.session.disconnect()
        self.destroy()


def main() -> int:
    app = CoralNetDownloaderApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
