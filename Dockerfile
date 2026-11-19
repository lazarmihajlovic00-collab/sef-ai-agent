FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY sef_agent ./sef_agent
RUN pip install --no-cache-dir ".[postgres]"
COPY --from=web /web/dist ./web/dist
COPY docs ./docs
ENV AS_OF_DATE=2026-11-19
EXPOSE 8000
CMD ["sh", "-c", "sef-agent seed --rules-only && sef-agent serve --host 0.0.0.0 --port 8000"]
