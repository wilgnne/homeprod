FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
COPY proxy/ ./proxy/
COPY tests/ ./tests/
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 11434

# Use the refactored package structure
CMD ["uvicorn", "proxy.main:app", "--host", "0.0.0.0", "--port", "11434"]
