web: python init_app.py && gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 4 --worker-class sync --access-logfile - --error-logfile -
