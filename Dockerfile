FROM python:3.12-slim

# System dependencies for Pillow and fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt constraints.txt ./
RUN pip install --no-cache-dir -r requirements.txt -c constraints.txt

# Copy application (VERSION zuerst: ändert sich selten, spart Cache-Läufe)
COPY VERSION ./VERSION
COPY . .

# Commit der gebauten Fassung, damit die Über-Seite ihn zeigen kann
ARG SOURCE_COMMIT=""
ENV PHOTOBOX_COMMIT=$SOURCE_COMMIT

# Create data directory
RUN mkdir -p /data

# Run with gunicorn
CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "run:app"]

EXPOSE 5000
