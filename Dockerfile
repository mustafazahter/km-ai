# komuta ai-bot imajı. Repo kökünden build edilir: docker build -t ai-bot .
# Komuta kaynaktan build eder; imaj yalnız linux/amd64.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src:/app \
    DATA_DIR=/data \
    PLUGINS_DIR=/app/plugins \
    HTTP_HOST=0.0.0.0 \
    HTTP_PORT=8080
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY stack_kit ./stack_kit
COPY stack_ui ./stack_ui
COPY src ./src
COPY plugins ./plugins

COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh

# Uygulama uid/gid 1000 ile çalışır; root gerektirmez. /data imajda 1000'e aittir (named volume ve
# Komuta diski bu sahipliği alır). Çalışma anında chown/setpriv YOK: disk sahipliğini değiştirmek
# Komuta'nın sonraki yayındaki disk hazırlama adımını bozar. Ayrıntı: docs/SECURITY.md
RUN useradd --create-home --uid 1000 --user-group app \
    && mkdir -p /data && chown 1000:1000 /data \
    && chmod 755 /usr/local/bin/entrypoint.sh
USER 1000

EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('HTTP_PORT','8080'), timeout=4)" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "ai_bot"]
