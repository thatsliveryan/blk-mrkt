"""
BLK MRKT — Lightweight HTTP server framework.
Replaces Flask using Python stdlib. Same route/blueprint pattern.
"""

import json
import re
import os
import mimetypes
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from io import BytesIO

# ---------------------------------------------------------------------------
# Simple Router
# ---------------------------------------------------------------------------

class Route:
    def __init__(self, method, pattern, handler):
        self.method = method
        # Convert /api/drops/:id to regex with named groups
        regex = re.sub(r':(\w+)', r'(?P<\1>[^/]+)', pattern)
        self.regex = re.compile(f'^{regex}$')
        self.handler = handler


class Blueprint:
    def __init__(self, name, prefix=''):
        self.name = name
        self.prefix = prefix
        self.routes = []

    def route(self, path, methods=None):
        if methods is None:
            methods = ['GET']
        def decorator(f):
            full_path = self.prefix + path
            for m in methods:
                self.routes.append(Route(m.upper(), full_path, f))
            return f
        return decorator


class App:
    def __init__(self, static_folder='static'):
        self.routes = []
        self.static_folder = static_folder

    def register_blueprint(self, bp):
        self.routes.extend(bp.routes)

    def route(self, path, methods=None):
        if methods is None:
            methods = ['GET']
        def decorator(f):
            for m in methods:
                self.routes.append(Route(m.upper(), path, f))
            return f
        return decorator

    def match(self, method, path):
        for route in self.routes:
            if route.method != method:
                continue
            m = route.regex.match(path)
            if m:
                return route.handler, m.groupdict()
        return None, {}


# ---------------------------------------------------------------------------
# Request / Response objects
# ---------------------------------------------------------------------------

class Request:
    def __init__(self):
        self.method = 'GET'
        self.path = ''
        self.query = {}
        self.headers = {}
        self.body = b''
        self._json = None
        self.content_type = ''
        self.files = {}
        self.form = {}
        self.path_params = {}

    def get_json(self, silent=False):
        if self._json is not None:
            return self._json
        try:
            self._json = json.loads(self.body.decode('utf-8'))
            return self._json
        except Exception:
            if silent:
                return {}
            raise

    @property
    def args(self):
        return self.query


class Response:
    def __init__(self, body='', status=200, headers=None, content_type='application/json'):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.content_type = content_type


def jsonify(data, status=200):
    return Response(json.dumps(data), status=status, content_type='application/json')


# ---------------------------------------------------------------------------
# Global request context (thread-local-like, fine for single-threaded)
# ---------------------------------------------------------------------------

class _G:
    """Simple attribute store for request-scoped data."""
    pass

g = _G()
request = Request()


# ---------------------------------------------------------------------------
# Multipart parser
# ---------------------------------------------------------------------------

def parse_multipart(body, boundary):
    """Parse multipart/form-data. Returns (fields_dict, files_dict)."""
    fields = {}
    files = {}
    parts = body.split(b'--' + boundary.encode())

    for part in parts:
        if part in (b'', b'--', b'--\r\n', b'\r\n'):
            continue
        part = part.strip(b'\r\n')
        if part == b'--':
            continue

        # Split headers from body
        if b'\r\n\r\n' in part:
            header_data, file_data = part.split(b'\r\n\r\n', 1)
        else:
            continue

        # Strip trailing boundary marker
        if file_data.endswith(b'\r\n'):
            file_data = file_data[:-2]

        headers_str = header_data.decode('utf-8', errors='replace')
        # Parse Content-Disposition
        name = None
        filename = None
        for line in headers_str.split('\r\n'):
            if 'Content-Disposition' in line:
                nm = re.search(r'name="([^"]*)"', line)
                fn = re.search(r'filename="([^"]*)"', line)
                if nm:
                    name = nm.group(1)
                if fn:
                    filename = fn.group(1)

        if name and filename:
            files[name] = type('File', (), {
                'filename': filename,
                'read': lambda d=file_data: d,
                'save': lambda path, d=file_data: open(path, 'wb').write(d),
                'data': file_data,
            })()
        elif name:
            fields[name] = file_data.decode('utf-8', errors='replace')

    return fields, files
