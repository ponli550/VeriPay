"""Vercel entrypoint: the real FastAPI engine, served as an ASGI function.
The chain signer (solders + keys) deliberately does not ship here."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from server import app  # noqa: F401  (Vercel serves `app`)
