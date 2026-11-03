FROM node:22-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
RUN npm run build

FROM python:3.12-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
# Same pinned Temporal development server used locally; persistent PoC only.
RUN curl -fsSL https://github.com/temporalio/cli/releases/download/v1.9.1/temporal_cli_1.9.1_linux_amd64.tar.gz -o /tmp/temporal.tgz \
    && echo '09a0326a51db84d02735e53542b9ebd8c4758daf47482a9ab0abce15844e60d5  /tmp/temporal.tgz' | sha256sum -c - \
    && tar -xzf /tmp/temporal.tgz -C /usr/local/bin temporal \
    && rm /tmp/temporal.tgz
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY harness ./harness
COPY scripts/serve_poc.py ./scripts/serve_poc.py
COPY --from=frontend /build/frontend/dist ./frontend/dist
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python", "scripts/serve_poc.py"]
