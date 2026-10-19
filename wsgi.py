"""Entry point for gunicorn and the flask command: `gunicorn wsgi:app`."""

from dotenv import load_dotenv

load_dotenv()

from orderflow import create_app  # noqa: E402

app = create_app()
