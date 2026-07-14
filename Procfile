web: gunicorn lunchnetsale.wsgi:application --workers 2 --threads 4 --worker-class gthread --timeout 30
release: python manage.py migrate --no-input
