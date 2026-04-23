"""
BLK MRKT — Main Application
Stdlib HTTP server with modular routing. No Flask needed.
"""

import os
import re
import json
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from config import DATA_DIR, AUDIO_DIR, COVERS_DIR, PORT, DEBUG, MAX_UPLOAD_SIZE
from models import init_db, get_db
from server import App, Blueprint, Request, Response, jsonify, g, parse_multipart
import server as srv
from engine import rate_limiter, RATE_LIMITS

# Create app
app = App(static_folder=os.path.join(os.path.dirname(__file__), 'static'))

# Import and register blueprints
from auth import auth_bp
from drops import drops_bp
from scenes import scenes_bp
from users import users_bp
from admin import admin_bp
from labels import labels_bp
from boosts import boosts_bp
from subscriptions import subs_bp
from payments import payments_bp
from follows import follows_bp
from badges import badges_bp
from analytics import analytics_bp
from connect import connect_bp
from dmca import dmca_bp, admin_dmca_bp
from tiers import tiers_bp

app.register_blueprint(auth_bp)
app.register_blueprint(drops_bp)
app.register_blueprint(scenes_bp)
app.register_blueprint(users_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(labels_bp)
app.register_blueprint(boosts_bp)
app.register_blueprint(subs_bp)
app.register_blueprint(payments_bp)
app.register_blueprint(follows_bp)
app.register_blueprint(badges_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(connect_bp)
app.register_blueprint(dmca_bp)
app.register_blueprint(admin_dmca_bp)
app.register_blueprint(tiers_bp)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
@app.route('/health')
def health(req):
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) as c FROM drops").fetchone()["c"]
    except Exception:
        count = 0
    finally:
        conn.close()
    return jsonify({"status": "alive", "drops": count})


# ---------------------------------------------------------------------------
# Admin reseed
# ---------------------------------------------------------------------------
@app.route('/api/admin/reseed')
def reseed(req):
    from config import ADMIN_SECRET
    # Accept secret via query param OR JSON body — never log it
    body = req.get_json(silent=True) or {}
    secret = req.query.get("secret") or body.get("secret", "")
    if not ADMIN_SECRET or not secret:
        return jsonify({"error": "Unauthorized"}, 403), 403
    # Constant-time compare to prevent timing-based secret enumeration
    import hmac as _hmac
    if not _hmac.compare_digest(str(secret), str(ADMIN_SECRET)):
        return jsonify({"error": "Unauthorized"}, 403), 403
    try:
        import seed
        result = seed.run_seed()
        return jsonify({"seeded": True, "result": result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}, 500), 500


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------
class BLKMRKTHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if DEBUG:
            BaseHTTPRequestHandler.log_message(self, format, *args)

    def _build_request(self):
        """Parse the incoming HTTP request into our Request object."""
        parsed = urlparse(self.path)
        req = Request()
        req.method = self.command
        req.path = parsed.path
        req.query = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()}
        req.headers = {k.lower(): v for k, v in self.headers.items()}
        req.content_type = req.headers.get('content-type', '')

        # Read body — enforce max upload size
        content_length = int(req.headers.get('content-length', 0))
        if content_length > MAX_UPLOAD_SIZE:
            self.send_response(413)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"error":"Request too large"}')
            return req
        if content_length > 0:
            req.body = self.rfile.read(content_length)

            # Parse multipart
            if 'multipart/form-data' in req.content_type:
                boundary_match = re.search(r'boundary=([^\s;]+)', req.content_type)
                if boundary_match:
                    boundary = boundary_match.group(1)
                    req.form, req.files = parse_multipart(req.body, boundary)

        # Set global request
        srv.request = req
        srv.g = type('G', (), {})()
        return req

    def _send_response(self, resp):
        """Send a Response object back to the client."""
        if isinstance(resp, tuple):
            body, status = resp if len(resp) == 2 else (resp[0], 200)
            if isinstance(body, Response):
                body.status = status
                resp = body
            else:
                resp = Response(json.dumps(body) if isinstance(body, dict) else str(body), status)

        self.send_response(resp.status)
        self.send_header('Content-Type', resp.content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, PATCH, DELETE, OPTIONS')

        # --- Security headers (fix #6 from security audit) ---
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.send_header('X-XSS-Protection', '0')   # Disabled — use CSP instead
        self.send_header('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        # CSP: only applies to HTML pages (not JSON API responses)
        if resp.content_type and 'html' in resp.content_type:
            self.send_header('Content-Security-Policy',
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://js.stripe.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data: blob: https:; "
                "media-src 'self' blob:; "
                "connect-src 'self' https://api.stripe.com; "
                "frame-src https://js.stripe.com https://hooks.stripe.com; "
                "worker-src blob:;"
            )

        for k, v in resp.headers.items():
            self.send_header(k, v)

        body = resp.body
        if isinstance(body, str):
            body = body.encode('utf-8')

        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _rate_limit_check(self, req):
        """
        Apply sliding-window rate limiting based on the route.
        Returns a Response if rate-limited, None if allowed.
        """
        path = req.path
        client_ip = req.headers.get('x-forwarded-for', '').split(',')[0].strip() or \
                    self.client_address[0]

        # Determine which rate limit bucket applies
        if path.startswith('/api/auth/'):
            # All auth endpoints share one bucket: login, register, forgot-password,
            # reset-password, resend-verification. Prevents brute-force on all of them.
            limit, window = RATE_LIMITS["auth"]
            key = f"ip:{client_ip}:auth"
        elif path.endswith('/access') and req.method == 'POST':
            limit, window = RATE_LIMITS["claim"]
            from server import g as _g
            uid = getattr(_g, 'user_id', None) or client_ip
            key = f"user:{uid}:claim"
        elif path.endswith('/engage') and req.method == 'POST':
            limit, window = RATE_LIMITS["engage"]
            from server import g as _g
            uid = getattr(_g, 'user_id', None) or client_ip
            key = f"user:{uid}:engage"
        elif path.startswith('/api/boosts') and req.method == 'POST':
            limit, window = RATE_LIMITS["boost"]
            from server import g as _g
            uid = getattr(_g, 'user_id', None) or client_ip
            key = f"user:{uid}:boost"
        else:
            return None  # No rate limit for other routes

        allowed, retry_after = rate_limiter.check(key, limit, window)
        if not allowed:
            resp = jsonify(
                {"error": f"Rate limited. Try again in {retry_after} seconds.", "retry_after": retry_after},
                status=429
            )
            resp.headers["Retry-After"] = str(retry_after)
            return resp
        return None

    def _handle(self):
        req = self._build_request()

        # OPTIONS (CORS preflight)
        if req.method == 'OPTIONS':
            self._send_response(Response('', 204))
            return

        # Rate limiting
        rate_resp = self._rate_limit_check(req)
        if rate_resp:
            self._send_response(rate_resp)
            return

        # Try API routes
        handler, params = app.match(req.method, req.path)
        if handler:
            req.path_params = params
            try:
                result = handler(req, **params) if params else handler(req)
                if isinstance(result, tuple):
                    resp_body, status = result
                    if isinstance(resp_body, Response):
                        resp_body.status = status
                        self._send_response(resp_body)
                    else:
                        self._send_response(Response(json.dumps(resp_body), status))
                elif isinstance(result, Response):
                    self._send_response(result)
                elif isinstance(result, dict):
                    self._send_response(jsonify(result))
                else:
                    self._send_response(Response(str(result)))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self._send_response(jsonify({"error": "Internal server error"}, status=500))
            return

        # Audio streaming with range requests
        audio_match = re.match(r'^/api/audio/([^/]+)$', req.path)
        if audio_match:
            self._serve_audio(audio_match.group(1), req)
            return

        # Cover images — basename only, no path traversal
        cover_match = re.match(r'^/api/covers/([^/]+)$', req.path)
        if cover_match:
            self._serve_file(COVERS_DIR, os.path.basename(cover_match.group(1)))
            return

        # Static files / SPA
        self._serve_static(req.path)

    def _serve_audio(self, drop_id, req):
        """Serve audio with range request support. Requires access for non-open drops."""
        conn = get_db()
        try:
            drop = conn.execute(
                "SELECT audio_path, r2_audio_key, status, drop_type, dmca_review FROM drops WHERE id = ?",
                (drop_id,)
            ).fetchone()
        finally:
            conn.close()

        if not drop or (not drop["audio_path"] and not drop["r2_audio_key"]):
            self._send_response(jsonify({"error": "Audio not found"}, status=404))
            return

        # DMCA block — drop is under review, audio cannot be served
        if drop["dmca_review"]:
            self._send_response(jsonify({
                "error": "This content is under DMCA review and cannot be streamed at this time."
            }, status=451))  # 451 Unavailable For Legal Reasons
            return

        if drop["status"] == "locked":
            self._send_response(jsonify({"error": "Drop is locked"}, status=403))
            return

        # For non-open drops, verify the user has claimed access
        if drop["drop_type"] != "open":
            from auth import decode_token
            auth_header = req.headers.get("authorization", "")
            authed = False
            if auth_header.startswith("Bearer "):
                payload = decode_token(auth_header[7:])
                if payload:
                    conn = get_db()
                    try:
                        access = conn.execute(
                            "SELECT id FROM drop_access WHERE user_id = ? AND drop_id = ?",
                            (payload["sub"], drop_id),
                        ).fetchone()
                        authed = access is not None
                    finally:
                        conn.close()
            if not authed:
                self._send_response(jsonify({"error": "Access required"}, status=403))
                return

        # Try local file first, fall back to R2
        local_path = None
        if drop["audio_path"]:
            filename = os.path.basename(drop["audio_path"])
            candidate = os.path.join(AUDIO_DIR, filename)
            if os.path.exists(candidate):
                local_path = candidate

        if local_path:
            # Serve from local filesystem with range support
            file_size = os.path.getsize(local_path)
            ext = local_path.rsplit(".", 1)[-1].lower()
            mime = "audio/mpeg" if ext == "mp3" else "audio/wav"

            range_header = req.headers.get('range', '')
            if range_header:
                match = re.search(r'bytes=(\d+)-(\d*)', range_header)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2)) if match.group(2) else file_size - 1
                    end = min(end, file_size - 1)
                    length = end - start + 1
                    with open(local_path, 'rb') as f:
                        f.seek(start)
                        data = f.read(length)
                    self.send_response(206)
                    self.send_header('Content-Type', mime)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Content-Length', str(length))
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    self.wfile.write(data)
                    return

            with open(local_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(file_size))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)

        elif drop["r2_audio_key"]:
            # Fall back to R2
            from storage import fetch_audio
            data, mime = fetch_audio(drop["r2_audio_key"])
            if not data:
                self._send_response(jsonify({"error": "Audio file missing"}, status=404))
                return
            self.send_response(200)
            self.send_header('Content-Type', mime or 'audio/mpeg')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)

        else:
            self._send_response(jsonify({"error": "Audio file missing"}, status=404))

    def _serve_file(self, directory, filename):
        """Serve a static file from a directory. Guards against path traversal."""
        # Resolve and confirm the file stays inside directory
        filepath = os.path.realpath(os.path.join(directory, filename))
        if not filepath.startswith(os.path.realpath(directory) + os.sep):
            self._send_response(jsonify({"error": "Forbidden"}, status=403))
            return
        if not os.path.isfile(filepath):
            self._send_response(jsonify({"error": "File not found"}, status=404))
            return

        mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
        with open(filepath, 'rb') as f:
            data = f.read()

        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path):
        """Serve static files or SPA fallback."""
        if path == '/':
            path = '/index.html'

        filepath = os.path.join(app.static_folder, path.lstrip('/'))

        if os.path.isfile(filepath):
            mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
            with open(filepath, 'rb') as f:
                data = f.read()

            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(data)
        else:
            # SPA fallback — serve index.html
            index_path = os.path.join(app.static_folder, 'index.html')
            if os.path.isfile(index_path):
                with open(index_path, 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_response(jsonify({"error": "Not found"}, status=404))

    def do_GET(self): self._handle()
    def do_POST(self): self._handle()
    def do_PUT(self): self._handle()
    def do_PATCH(self): self._handle()
    def do_DELETE(self): self._handle()
    def do_OPTIONS(self): self._handle()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(COVERS_DIR, exist_ok=True)
    init_db()

    server = HTTPServer(('0.0.0.0', PORT), BLKMRKTHandler)
    print(f'\n  \033[1;31m██████╗ ██╗     ██╗  ██╗    ███╗   ███╗██████╗ ██╗  ██╗████████╗\033[0m')
    print(f'  \033[1;31m██╔══██╗██║     ██║ ██╔╝    ████╗ ████║██╔══██╗██║ ██╔╝╚══██╔══╝\033[0m')
    print(f'  \033[1;31m██████╔╝██║     █████╔╝     ██╔████╔██║██████╔╝█████╔╝    ██║   \033[0m')
    print(f'  \033[1;31m██╔══██╗██║     ██╔═██╗     ██║╚██╔╝██║██╔══██╗██╔═██╗    ██║   \033[0m')
    print(f'  \033[1;31m██████╔╝███████╗██║  ██╗    ██║ ╚═╝ ██║██║  ██║██║  ██╗   ██║   \033[0m')
    print(f'  \033[1;31m╚═════╝ ╚══════╝╚═╝  ╚═╝    ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝   \033[0m')
    print(f'\n  🔥 BLK MRKT running on http://0.0.0.0:{PORT}')
    print(f'  📁 Data: {DATA_DIR}')
    print(f'  🎵 Audio: {AUDIO_DIR}\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n  Shutting down...')
        server.server_close()
