"""FinVerify frontend server — FastAPI.

Serves the self-contained web UI and streams backend.analyze_stream()
as NDJSON so the browser renders the CI pipeline live. The uploaded
file exists on disk only for the duration of the analysis: it is
written to a private temp path and unlinked in a finally block the
moment the stream ends — retention is the length of the request.

Run: uv run uvicorn server:app --port 7861   (or: uv run python server.py)
"""

import json
import os
import tempfile

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

import backend

app = FastAPI(title="FinVerify")

_WEB = os.path.join(os.path.dirname(__file__), "web", "index.html")


@app.get("/")
def index():
    return FileResponse(_WEB, media_type="text/html")


@app.get("/api/contribution")
def contribution():
    """Developer tips / contribution target. The address comes from .env
    (a Hata deposit address on Solana, or any wallet); the QR is generated
    locally from the real value — no placeholder image, no hotlink. With
    nothing configured the endpoint 204s and the card never renders."""
    address = os.environ.get("VERIPAY_WALLET_ADDRESS", "").strip()
    if not address:
        return Response(status_code=204)
    import segno
    target = os.environ.get("VERIPAY_WALLET_URL", "").strip() or address
    qr = segno.make(target, error="m").svg_data_uri(scale=4, dark="#e2e2e2", light=None)
    return {"address": address, "url": target, "qr": qr}


@app.post("/api/analyze")
async def analyze(file: UploadFile, question: str = Form(...)):
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".pdf"
    fd, path = tempfile.mkstemp(prefix="finverify_", suffix=suffix)
    with os.fdopen(fd, "wb") as out:
        out.write(await file.read())

    def stream():
        try:
            for event in backend.analyze_stream(path, question):
                yield json.dumps(event) + "\n"
        finally:
            # Retention = duration of the request. Nothing to purge later.
            try:
                os.unlink(path)
            except OSError:
                pass

    return StreamingResponse(stream(), media_type="application/x-ndjson")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=7861)
