web: gunicorn 'web_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --worker-class gthread --threads 4 --timeout 120 --max-requests 500 --max-requests-jitter 50 --access-logfile - --error-logfile -
