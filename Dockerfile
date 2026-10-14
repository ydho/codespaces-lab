# ---- builder：只用來安裝套件 ----
FROM python:3.12-slim AS builder
WORKDIR /install
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install/deps -r requirements.txt

# ---- runtime：不含建置工具，體積更小、攻擊面更小 ----
FROM python:3.12-slim
ARG GIT_SHA=dev
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/deps/bin:$PATH" \
    PYTHONPATH="/deps/lib/python3.12/site-packages" \
    GIT_SHA=$GIT_SHA
COPY --from=builder /install/deps /deps
WORKDIR /srv
COPY app/ ./app/
RUN useradd --create-home --uid 10001 appuser
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]