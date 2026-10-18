FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update \
	&& apt-get install -y --no-install-recommends libexpat1 \
	&& rm -rf /var/lib/apt/lists/*
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY app.py config.py dem_processing.py facilities_fetcher.py mtr_facilities.py osm_index.py pedestrian_index.py ./
EXPOSE 5000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=30s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=3)"]
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:5000", "app:app"]
