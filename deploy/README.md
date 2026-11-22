# Deployment notes

The stack is four services: PostgreSQL, Qdrant, the API image (`backend/Dockerfile`) and the web
image (`frontend/Dockerfile`, nginx serving the build and proxying `/api`).

## Single VM (simplest)

```bash
git clone <repo> && cd production-rag-service
cp .env.example .env      # APP_ENV=production, JWT_SECRET_KEY=$(openssl rand -hex 32), provider keys
docker compose up -d --build
```

Put a TLS-terminating reverse proxy (Caddy or nginx with Let's Encrypt) in front of port 8080.
Ports 5432/6333/8000 are bound to `127.0.0.1` in `docker-compose.yml`, so only the web port is
public.

## Managed services (free/low-cost tiers)

| Piece | Option | Setting |
| --- | --- | --- |
| PostgreSQL | Neon / Supabase | `DATABASE_URL=postgresql+asyncpg://...` (add `?ssl=require` if needed) |
| Qdrant | Qdrant Cloud free cluster | `QDRANT_URL`, `QDRANT_API_KEY` |
| API | Render / Railway / Fly.io from `backend/Dockerfile` | the image reads `PORT`; set all env vars from `.env.example` |
| Web | any static host or the web image | for a static host, build with `VITE_API_URL=https://<api-host>` and add that origin to `CORS_ORIGINS` |

## Production checklist

* `APP_ENV=production` (the app refuses to start with a weak JWT secret or fake providers).
* `USE_FAKE_PROVIDERS=false`; `GET /health` shows `configured` for Cohere and Anthropic.
* `CORS_ORIGINS` lists only the real web origin.
* One API instance (rate limits are in memory), or move rate limiting to Redis first.
* Back up the PostgreSQL database and Qdrant storage volume.
