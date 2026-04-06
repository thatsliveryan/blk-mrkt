import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", "/data")

DB_PATH = os.path.join(DATA_DIR, "blkmrkt.db")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
COVERS_DIR = os.path.join(DATA_DIR, "covers")

JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("PRODUCTION"):
        raise RuntimeError("JWT_SECRET env var is required in production")
    JWT_SECRET = "blkmrkt-dev-secret-local-only"
JWT_ACCESS_EXPIRY = 3600       # 1 hour
JWT_REFRESH_EXPIRY = 86400 * 30  # 30 days

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

PORT = int(os.environ.get("PORT", 8080))
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
