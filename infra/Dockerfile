# syntax=docker/dockerfile:1.7
# One image for API, worker, migrations and admin CLI. Web clients are built in
# isolated stages that never see backend or model secrets.

FROM node:24-bookworm-slim AS student-web
WORKDIR /src/apps/student-web
COPY contracts /src/contracts
COPY apps/staff-web/src/api/contracts.generated.ts /src/apps/staff-web/src/api/contracts.generated.ts
COPY apps/student-web/package.json apps/student-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/student-web ./
RUN npm run build

FROM node:24-bookworm-slim AS staff-web
WORKDIR /src/apps/staff-web
COPY contracts /src/contracts
COPY apps/staff-web/package.json apps/staff-web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/staff-web ./
RUN npm run build

FROM python:3.14-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --system --gid 10001 xueban && useradd --system --uid 10001 --gid xueban --home-dir /app --shell /usr/sbin/nologin xueban
WORKDIR /app
COPY services/backend/requirements.txt services/backend/requirements.txt
RUN pip install -r services/backend/requirements.txt
COPY contracts contracts
COPY services/backend services/backend
COPY --from=student-web /src/apps/student-web/dist apps/student-web/dist
COPY --from=staff-web /src/apps/staff-web/dist apps/staff-web/dist
RUN rm -rf services/backend/tests services/backend/.venv && find /app/apps -name '*.map' -delete \
    && mkdir -p /data/assets /ledger && chown xueban:xueban /data/assets /ledger && chmod 700 /data/assets /ledger
ENV PYTHONPATH=/app/services/backend \
    CONTRACTS_DIR=/app/contracts/phase1 \
    STUDENT_WEB_DIST=/app/apps/student-web/dist \
    STAFF_WEB_DIST=/app/apps/staff-web/dist
USER xueban
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "xueban_backend.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "*"]
