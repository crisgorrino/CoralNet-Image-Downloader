"""Ephemeral, exact-host CoralNet session; no credential or cookie persistence."""
from __future__ import annotations

import re
from threading import Event
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

HOST = "coralnet.ucsd.edu"


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path,
                       "[REDACTED]" if parts.query else "", ""))


def safe_error(exc: BaseException) -> str:
    """Never serialize transport exception messages, headers, URLs or bodies."""
    if isinstance(exc, AccessError):
        return str(exc)
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}"
    # App errors use fixed strings. Unknown/OS/transport errors can contain secrets.
    if type(exc) in {RuntimeError, ValueError}:
        message = re.sub(r"https?://[^\s<>\"']+", lambda m: sanitize_url(m[0]), str(exc))
        if re.search(r"cookie|sessionid|csrf|authorization|password|token|signature", message, re.I):
            return "Request failed; reconnect if necessary."
        return message
    return type(exc).__name__ + ": operation failed."


class AccessError(RuntimeError):
    def __init__(self, state: str, message: str):
        super().__init__(message)
        self.state = state


class SignedURLExpired(RuntimeError):
    pass


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.username or new.password:
            raise RuntimeError("Unsafe download redirect refused.")
        if old.hostname == HOST and (new.hostname != HOST or new.port not in {None, 443}):
            raise RuntimeError("Unexpected CoralNet redirect refused.")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            # No secrets may travel across a redirect's origin boundary.
            if old.hostname != new.hostname:
                redirected.remove_header("Cookie")
                redirected.remove_header("Authorization")
            elif old.hostname == HOST and req.get_header("Cookie"):
                redirected.add_unredirected_header("Cookie", req.get_header("Cookie"))
        return redirected


class CoralNetSession:
    def __init__(self):
        self._session_cookie = ""
        self.state = "Not connected"
        self._opener = build_opener(_SafeRedirect())
        self._storage_opener = build_opener(_SafeRedirect())

    def is_authenticated(self) -> bool:
        return bool(self._session_cookie) and self.state in {"Connected", "No access"}

    def disconnect(self):
        self._session_cookie = ""
        self.state = "Not connected"

    def open(self, request: Request, timeout: float):
        parts = urlsplit(request.full_url)
        if parts.scheme != "https" or parts.username or parts.password or parts.port not in {None, 443}:
            raise RuntimeError("Only HTTPS image and CoralNet URLs are allowed.")
        coralnet = parts.hostname == HOST
        request.remove_header("Cookie")
        request.remove_header("Authorization")
        request.remove_header("X-csrftoken")
        if coralnet and self._session_cookie:
            request.add_unredirected_header("Cookie", "sessionid=" + self._session_cookie)
        try:
            return (self._opener if coralnet else self._storage_opener).open(request, timeout=timeout)
        except HTTPError as exc:
            if coralnet and exc.code in {401, 403}:
                exc.close()
                self.deny(login=exc.code == 401)
            if not coralnet and exc.code in {401, 403, 410} and parts.query:
                exc.close()
                raise SignedURLExpired("Temporary image URL expired.") from None
            raise

    def deny(self, login: bool):
        if login:
            connected = bool(self._session_cookie)
            self.disconnect()
            self.state = "Session expired" if connected else "Not connected"
            message = ("Your CoralNet session has expired. Reconnect to CoralNet." if connected
                       else "This CoralNet source requires authentication. Connect to CoralNet and try again.")
        else:
            self.state = "No access" if self._session_cookie else "Not connected"
            message = ("Your CoralNet account does not have permission to access this source." if self._session_cookie
                       else "You are not authenticated or your CoralNet account does not have access to this source. Connect to CoralNet and try again.")
        raise AccessError(self.state, message) from None

    def check_html(self, document: str, final_url: str):
        path = urlsplit(final_url).path.lower()
        login = "/accounts/login" in path or bool(re.search(
            r'<input\b[^>]*type=["\']password["\']', document, re.I))
        if login:
            self.deny(login=True)
        text = re.sub(r"<[^>]+>", " ", document).lower()
        if re.search(r"(?:don&#x27;t|don't|do not|don’t) have permission|access denied|permission denied|not authorized", text):
            self.deny(login=False)
        # An invalidated cookie can still browse public sources. Detect expiry there too.
        if self._session_cookie and re.search(r'href=["\']/accounts/login', document, re.I):
            self.deny(login=True)
        if self._session_cookie and re.search(r'(?:href|action)=["\']/accounts/logout/?["\'?]', document, re.I):
            self.state = "Connected"

    def connect(self, cookie: str, stop_event: Event, timeout: float = 30):
        """Import only sessionid, then verify via CoralNet's normal signed-in UI."""
        self.disconnect()
        if not re.fullmatch(r"[A-Za-z0-9_\-:.=]+", cookie.strip()) or len(cookie.strip()) > 4096:
            raise ValueError("Paste only the CoralNet session cookie value.")
        self._session_cookie = cookie.strip()
        try:
            if stop_event.is_set():
                raise RuntimeError("Connection cancelled.")
            request = Request(f"https://{HOST}/", headers={"User-Agent": "CoralNetImageDownloader/4.0", "Accept-Encoding": "identity"})
            with self.open(request, timeout) as response:
                document = response.read().decode("utf-8", errors="replace")
                self.check_html(document, response.geturl())
            if not re.search(r'(?:href|action)=["\']/accounts/logout/?["\'?]', document, re.I):
                self.deny(login=True)
            if stop_event.is_set():
                raise RuntimeError("Connection cancelled.")
            self.state = "Connected"
        except BaseException:
            state = self.state
            self.disconnect()
            self.state = state if state in {"Session expired", "No access"} else "Not connected"
            raise
