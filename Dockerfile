# Production image: Python standard library only, no build step.
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
WORKDIR /srv/app
COPY app.py seo.py legal.py totp.py index.html admin.html offline.html sw.js manifest.webmanifest admin-manifest.webmanifest ./
COPY assets ./assets
COPY icons ./icons
# Behind the bundled Caddy proxy (docker-compose.yml): trust X-Forwarded-*, write JSON logs, back up daily.
ENV HOST=0.0.0.0 PORT=4173 DATA_DIR=/data TRUST_PROXY=1 LOG_FORMAT=json AUTO_BACKUP=1 PYTHONUNBUFFERED=1
VOLUME /data
USER app
EXPOSE 4173
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:4173/api/health', timeout=4).status == 200 else 1)"
CMD ["python3", "-I", "app.py"]
