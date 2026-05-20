FROM python:3.11-slim

WORKDIR /app

# =========================
# System Dependencies
# =========================
RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# =========================
# Environment Variables
# =========================
ENV GDAL_CONFIG=/usr/bin/gdal-config
ENV MPLBACKEND=Agg
ENV PYTHONUNBUFFERED=1

# =========================
# Install Python Packages
# =========================
COPY requirements.txt .

RUN pip install --upgrade pip

# install heavy deps first
RUN pip install numpy rasterio

RUN pip install --no-cache-dir -r requirements.txt

# =========================
# Copy Project
# =========================
COPY . .

# =========================
# Create Uploads Folder
# =========================
RUN mkdir -p uploads

# =========================
# Expose Port
# =========================
EXPOSE 5000

# =========================
# Run Flask App
# =========================
CMD ["gunicorn", "--workers", "1", "--threads", "2", "--timeout", "1200", "--worker-class", "gthread", "--bind", "0.0.0.0:5000", "app:app"]