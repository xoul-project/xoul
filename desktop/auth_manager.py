"""
Xoul Desktop Auth Manager

Handles OAuth2 System Browser flow:
1. Opens system browser to login page
2. Starts local HTTP server to receive callback with JWT token
3. Stores token in ~/.xoul/auth.json
4. Provides token for WebView and API calls
"""

import http.server
import json
import os
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from i18n import t

# ── Config ──

AUTH_FILE = Path(os.path.expanduser("~")) / ".xoul" / "auth.json"
CALLBACK_PORT = 19283

def _load_web_url():
    """config.json에서 web.backend_url 로드"""
    for cfg_path in [
        Path(__file__).resolve().parent.parent / "config.json",
        Path("config.json"),
    ]:
        if cfg_path.is_file():
            try:
                with open(cfg_path, "r", encoding="utf-8-sig") as f:
                    cfg = json.load(f)
                return cfg.get("web", {}).get("backend_url", "http://localhost:8080")
            except Exception:
                pass
    return "http://localhost:8080"

WEB_SERVICE_URL = _load_web_url()


def _ensure_dir():
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)


# ── Token Storage ──

def get_token() -> str | None:
    """Read stored JWT token."""
    if not AUTH_FILE.exists():
        return None
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return data.get("token")
    except Exception:
        return None


def get_user() -> dict | None:
    """Read stored user info."""
    if not AUTH_FILE.exists():
        return None
    try:
        data = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        return data.get("user")
    except Exception:
        return None


def save_auth(token: str, user: dict | None = None):
    """Save JWT token and optional user info."""
    _ensure_dir()
    data = {"token": token}
    if user:
        data["user"] = user
    AUTH_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_auth():
    """Remove stored auth."""
    if AUTH_FILE.exists():
        AUTH_FILE.unlink()


def is_logged_in() -> bool:
    return get_token() is not None


# ── System Browser OAuth Flow ──

class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Handles the OAuth callback on localhost."""

    token: str | None = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        token = params.get("token", [None])[0]

        if token:
            _CallbackHandler.token = token
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("""
            <html>
            <head><style>
                body { font-family: 'Segoe UI', sans-serif; background: #1f1f1f; color: #e8e8e8;
                       display: flex; align-items: center; justify-content: center; height: 100vh; }
                .card { text-align: center; padding: 48px; background: #252525; border-radius: 16px;
                        border: 1px solid #3a3a3a; }
                h1 { font-size: 24px; margin-bottom: 8px; }
                p { color: #a0a0a0; font-size: 14px; }
            </style></head>
            <body><div class="card">
                <h1>✅ {t('auth.login_success_title', **{})}</h1>
                <p>{t('auth.login_success_msg', **{})}</p>
            </div></body>
            </html>
            """.encode("utf-8"))
        else:
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>Login failed</h1><p>No token received.</p>")

    def log_message(self, format, *args):
        pass  # Suppress server logs


def login_with_browser(provider: str = "google", callback=None):
    """
    Open system browser for OAuth login.

    Args:
        provider: "google" or "facebook"
        callback: optional function called with (token, user) on success

    This runs in a background thread and calls callback on the main thread
    (via the returned token).
    """
    callback_url = f"http://localhost:{CALLBACK_PORT}/callback"
    login_url = f"{WEB_SERVICE_URL}/api/auth/{provider}?redirect_uri={callback_url}"

    def _run():
        # Start local server to receive callback
        _CallbackHandler.token = None
        server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _CallbackHandler)
        server.timeout = 120  # 2 minute timeout

        # Open browser
        webbrowser.open(login_url)

        # Wait for callback (single request)
        server.handle_request()
        server.server_close()

        if _CallbackHandler.token:
            save_auth(_CallbackHandler.token)
            # Try to fetch user profile
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{WEB_SERVICE_URL}/api/auth/me",
                    headers={"Authorization": f"Bearer {_CallbackHandler.token}"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    user = json.loads(resp.read().decode())
                    save_auth(_CallbackHandler.token, user)
                    if callback:
                        callback(_CallbackHandler.token, user)
            except Exception:
                if callback:
                    callback(_CallbackHandler.token, None)
        else:
            if callback:
                callback(None, None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def login_with_email(email: str, password: str) -> tuple[str | None, dict | None]:
    """
    Login with email/password directly.
    Returns (token, user) or (None, None) on failure.
    """
    import urllib.request
    import urllib.error

    body = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        f"{WEB_SERVICE_URL}/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("token")
            user = data.get("user")
            if token:
                save_auth(token, user)
            return token, user
    except urllib.error.HTTPError:
        return None, None


def signup_with_email(email: str, password: str, name: str = "") -> tuple[str | None, dict | None]:
    """
    Signup with email/password.
    Returns (token, user) or (None, None) on failure.
    """
    import urllib.request
    import urllib.error

    body = json.dumps({"email": email, "password": password, "name": name}).encode()
    req = urllib.request.Request(
        f"{WEB_SERVICE_URL}/api/auth/signup",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("token")
            user = data.get("user")
            if token:
                save_auth(token, user)
            return token, user
    except urllib.error.HTTPError:
        return None, None
