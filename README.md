# BLK MRKT

Scarcity-driven music drop platform. Drops. Not streams.

## Quick Start

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:8080

## Deploy to Railway

1. Push to GitHub
2. Connect repo in Railway
3. Add a volume mounted at `/data`
4. Set env vars: `JWT_SECRET`, `PORT=8080`
5. Deploy

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `8080` | Server port |
| `DATA_DIR` | `/data` | Persistent storage path |
| `JWT_SECRET` | dev secret | JWT signing key (change in prod!) |
| `FLASK_DEBUG` | `0` | Enable debug mode |
