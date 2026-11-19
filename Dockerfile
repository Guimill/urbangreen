FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN DEBUG=0 SECRET_KEY=build-only python manage.py collectstatic --noinput
EXPOSE 8000
# One worker: the "Analyser" button runs the download in a thread of this process.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 120 --access-logfile -"]
