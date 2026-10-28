# The inventory server. Build from the repo root (it needs src/phone.html, the page the PC serves too):
#   docker build -f server/Dockerfile -t atf-inventory .      or: cd server && docker compose up -d
FROM python:3.13-slim
WORKDIR /app
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 1000 --create-home atf && mkdir /data && chown atf /data
COPY server/atf_server ./atf_server
COPY src/phone.html ./atf_server/phone.html
USER atf
ENV ATF_DATA=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
HEALTHCHECK --interval=1m --timeout=5s CMD python -c "import ssl, urllib.request; urllib.request.urlopen('https://127.0.0.1:8765/api/hello', context=ssl._create_unverified_context(), timeout=4)"
CMD ["python", "-m", "atf_server", "serve"]
