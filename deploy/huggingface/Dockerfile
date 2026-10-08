# Hugging Face Space (Docker). Python standard library only, no build step.
FROM python:3.13-slim
RUN useradd -m -u 1000 user
USER user
WORKDIR /home/user/app
COPY --chown=user app.py seo.py legal.py totp.py index.html admin.html offline.html sw.js manifest.webmanifest admin-manifest.webmanifest ./
COPY --chown=user assets ./assets
COPY --chown=user icons ./icons
# The Space filesystem is temporary: data added after deploy is lost when the Space restarts.
ENV HOST=0.0.0.0 PORT=7860 DATA_DIR=/home/user/data TRUST_PROXY=1 LOG_FORMAT=json PYTHONUNBUFFERED=1
EXPOSE 7860
CMD ["python3", "-I", "app.py"]
