FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --system --uid 1000 lukis
USER lukis
EXPOSE 8000
# Only reachable from Next.js on the compose network, so the X-Forwarded-For
# it passes on (set by Caddy to the visitor's address) can be trusted.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", \
     "--log-config", "logging.json"]
