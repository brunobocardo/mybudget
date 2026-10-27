FROM python:3.12-slim
WORKDIR /app
COPY app/ /app/
ENV HOST=0.0.0.0 PORT=8765 DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8765/health')" || exit 1
CMD ["python", "server.py"]
