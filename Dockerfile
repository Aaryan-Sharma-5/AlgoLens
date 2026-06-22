# AlgoLens — single container running FastAPI (8000) + Next.js (3000).
FROM python:3.11-slim

# Node.js 20 + npm. Debian bookworm's apt `nodejs` is 18.x, but Next.js 16
# requires Node >=20.9, so install Node 20 from NodeSource (still on the
# python:3.11-slim base, which also provides Linux multiprocessing + resource
# limits the sandbox depends on).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version && npm --version \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Backend deps (cached unless requirements.txt changes).
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Frontend deps (cached unless package files change). Linux node_modules are
# built here; host node_modules are excluded via .dockerignore so the later
# COPY can't clobber them.
COPY frontend/package.json frontend/package-lock.json ./frontend/
WORKDIR /app/frontend
# Retry flags make the install resilient to transient registry connection resets.
RUN npm ci --no-audit --no-fund \
    --fetch-retries=5 --fetch-retry-factor=2 \
    --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000

# App source.
WORKDIR /app
COPY . .

# Build the Next.js production bundle.
RUN cd frontend && npm run build

EXPOSE 8000 3000

# Run uvicorn in the background and Next.js in the foreground (PID 1's child).
CMD uvicorn backend.main:app --host 0.0.0.0 --port 8000 & cd frontend && npm run start -- -p 3000
