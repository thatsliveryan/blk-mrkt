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
JWT_ACCESS_EXPIRY = 3600         # 1 hour
JWT_REFRESH_EXPIRY = 86400 * 30  # 30 days

MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

ALLOWED_AUDIO_EXTENSIONS = {"mp3", "wav"}
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

PORT = int(os.environ.get("PORT", 8080))
DEBUG = os.environ.get("DEBUG", "0") == "1"

# Stripe
STRIPE_SECRET_KEY        = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET    = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PUBLISHABLE_KEY   = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_CONNECT_ENABLED   = os.environ.get("STRIPE_CONNECT_ENABLED", "false").lower() == "true"

# Public base URL (used to build Stripe redirect URLs + emails)
PUBLIC_URL = os.environ.get("PUBLIC_URL", "https://blk-mrkt-production.up.railway.app")

# Cloudflare R2 object storage (optional — falls back to local if not set)
R2_ACCOUNT_ID  = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY  = os.environ.get("R2_ACCESS_KEY", "")
R2_SECRET_KEY  = os.environ.get("R2_SECRET_KEY", "")
R2_BUCKET      = os.environ.get("R2_BUCKET", "blk-mrkt-media")
R2_ENABLED     = bool(R2_ACCOUNT_ID and R2_ACCESS_KEY and R2_SECRET_KEY)

# Email provider (optional — registration works without it, just no verification emails)
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "")   # "resend" | "sendgrid" | "mailgun"
EMAIL_API_KEY  = os.environ.get("EMAIL_API_KEY", "")
EMAIL_FROM     = os.environ.get("EMAIL_FROM", "noreply@blkmrkt.com")
EMAIL_ENABLED  = bool(EMAIL_API_KEY and EMAIL_PROVIDER)

# Admin seed key
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "blkmrkt-admin-dev")
