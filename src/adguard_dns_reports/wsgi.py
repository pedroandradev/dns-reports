"""WSGI entrypoint: gunicorn adguard_dns_reports.wsgi:app."""
from . import create_app
app = create_app()
