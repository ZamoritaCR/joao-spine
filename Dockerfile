FROM python:3.12-slim

WORKDIR /app

# Install system deps for asyncssh
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install chromium for /browse endpoints (playwright). --with-deps pulls in
# all required system libraries via apt. Adds ~400MB to the final image but
# makes the /browse/* endpoints functional on Railway.
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 8000

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
