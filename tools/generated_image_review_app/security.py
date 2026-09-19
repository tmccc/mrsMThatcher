"""Shared authentication and request authority for both image review workflows."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from starlette.responses import PlainTextResponse

MAX_REQUEST_BYTES = 1024 * 1024


def is_loopback(host: str) -> bool:
    """Recognise loopback without resolving user-controlled hostnames."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_binding(host, username, password, *, tls=False, trusted_proxy_origin=None):
    """Require credentials and protected transport for a non-loopback listener."""
    if bool(username) != bool(password):
        raise ValueError("both review credentials are required")
    if trusted_proxy_origin:
        parsed = urlsplit(trusted_proxy_origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("trusted proxy origin must be an exact HTTPS origin")
    if not is_loopback(host):
        if not username or not password:
            raise ValueError("LAN binding requires MRS_REVIEW_USERNAME and MRS_REVIEW_PASSWORD")
        if not tls and not trusted_proxy_origin:
            raise ValueError("LAN binding requires TLS or an explicit trusted HTTPS reverse proxy")


class ReviewSecurity:
    """Bound and authenticate each complete request before invoking review routes."""

    def __init__(self, app, *, username=None, password=None, secret_key=None, allowed_hosts=("localhost", "127.0.0.1", "::1"), trusted_proxy_origin=None):
        """Configure credentials, request origin and token-signing authority."""
        if bool(username) != bool(password):
            raise ValueError("both review credentials are required")
        self.app = app
        self.username, self.password = username, password
        self.secret = (secret_key or secrets.token_urlsafe(32)).encode()
        self.allowed_hosts = frozenset(allowed_hosts)
        self.proxy_origin = trusted_proxy_origin.rstrip("/") if trusted_proxy_origin else None
        if self.proxy_origin:
            validate_binding("0.0.0.0", username, password, trusted_proxy_origin=self.proxy_origin)

    def signed_csrf(self):
        """Issue a fresh authenticated anti-CSRF token."""
        nonce = secrets.token_urlsafe(24)
        return nonce + "." + hmac.new(self.secret, nonce.encode(), hashlib.sha256).hexdigest()

    def valid_csrf(self, token, cookie):
        """Bind a supplied token to its signed same-site cookie."""
        if not token or not cookie or not hmac.compare_digest(token.encode(), cookie.encode()):
            return False
        try:
            nonce, signature = token.rsplit(".", 1)
        except ValueError:
            return False
        return hmac.compare_digest(signature.encode(), hmac.new(self.secret, nonce.encode(), hashlib.sha256).hexdigest().encode())

    async def __call__(self, scope, receive, send):
        """Reject hostile requests before body parsing, image access or mutations."""
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]}
        async def reject(code, message):
            security_headers = {
                "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            }
            if code == 401:
                security_headers["WWW-Authenticate"] = 'Basic realm="Image review"'
            response = PlainTextResponse(message, code, headers=security_headers)
            await response(scope, receive, send)
        try:
            authority = headers.get("host", "")
            parsed = urlsplit("//" + authority)
            if parsed.hostname not in self.allowed_hosts or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or len([k for k, _ in scope["headers"] if k.lower() == b"host"]) != 1:
                return await reject(400, "Invalid Host")
            port = parsed.port
        except ValueError:
            return await reject(400, "Invalid Host")
        if self.username and self.password:
            try:
                scheme, encoded = headers.get("authorization", "").split(" ", 1)
                user, password = base64.b64decode(encoded, validate=True).decode().split(":", 1)
            except (ValueError, UnicodeError):
                scheme, user, password = "", "", ""
            user_ok = hmac.compare_digest(hashlib.sha256(user.encode()).digest(), hashlib.sha256(self.username.encode()).digest())
            pass_ok = hmac.compare_digest(hashlib.sha256(password.encode()).digest(), hashlib.sha256(self.password.encode()).digest())
            if scheme.lower() != "basic" or not (user_ok & pass_ok):
                return await reject(401, "Authentication required")
        server = scope.get("server")
        local_listener = bool(server and is_loopback(str(server[0])))
        if not local_listener or not is_loopback(parsed.hostname):
            if not (self.username and self.password):
                return await reject(401, "Authentication required")
            if scope.get("scheme") != "https" and not self.proxy_origin:
                return await reject(403, "Protected transport required")
        origin = self.proxy_origin or f"{scope.get('scheme', 'http')}://{authority}"
        if headers.get("origin") and headers["origin"] != origin:
            return await reject(403, "Cross-origin request rejected")
        if headers.get("sec-fetch-site") == "cross-site":
            return await reject(403, "Cross-site request rejected")
        length = headers.get("content-length")
        if length is not None and (not length.isdigit() or int(length) > MAX_REQUEST_BYTES):
            return await reject(413, "Request body too large")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            block = message.get("body", b"")
            if len(body) + len(block) > MAX_REQUEST_BYTES:
                return await reject(413, "Request body too large")
            body.extend(block)
            if not message.get("more_body"):
                break
        try:
            cookies = SimpleCookie(headers.get("cookie", ""))
            cookie = cookies["review_csrf"].value if "review_csrf" in cookies else ""
        except Exception:
            cookie = ""
        token = cookie if self.valid_csrf(cookie, cookie) else self.signed_csrf()
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            supplied = headers.get("x-csrf-token", "")
            if not supplied and headers.get("content-type", "").split(";", 1)[0] == "application/x-www-form-urlencoded":
                try:
                    supplied = parse_qs(bytes(body).decode(), max_num_fields=1000).get("csrf", [""])[0]
                except (ValueError, UnicodeError):
                    return await reject(400, "Malformed form")
            if not self.valid_csrf(supplied, cookie):
                return await reject(403, "CSRF validation failed")
        scope.setdefault("state", {})["review_csrf"] = token
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        async def secured_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-frame-options", b"DENY"), (b"x-content-type-options", b"nosniff"),
                    (b"content-security-policy", b"default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                    (b"referrer-policy", b"no-referrer"), (b"cache-control", b"no-store"),
                    (b"x-csrf-token", token.encode()),
                ]
                if token != cookie:
                    value = f"review_csrf={token}; HttpOnly; Path=/; SameSite=Strict" + ("; Secure" if origin.startswith("https:") else "")
                    message["headers"].append((b"set-cookie", value.encode()))
            await send(message)
        await self.app(scope, replay, secured_send)
