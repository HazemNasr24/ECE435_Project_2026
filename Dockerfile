FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

ENV GDAL_CONFIG=/usr/bin/gdal-config
ENV MPLBACKEND=Agg

COPY requirements.txt .

RUN pip install --upgrade pip

RUN pip install numpy
RUN pip install rasterio

RUN pip install -r requirements.txt

COPY . .

RUN mkdir -p uploads

EXPOSE 5000

CMD ["gunicorn", \
"--workers", "1", \
"--threads", "2", \
"--timeout", "600", \
"--bind", "0.0.0.0:5000", \
"app:app"]