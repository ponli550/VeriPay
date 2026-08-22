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

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import backend
import explorer

app = FastAPI(title="FinVerify")
_hata_cached = None
# Releases THIS process produced, keyed by audit root — the owner-signed
# payment path only builds transactions for results the server itself
# verified and released. Ephemeral by design (LRU 20).
_released: dict = {}
app.mount("/vendor", StaticFiles(directory=os.path.join(
    os.path.dirname(__file__), "web", "vendor")), name="vendor")

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
    source = "env"
    if not address and os.environ.get("HATA_API_KEY"):
        # Live read-only fetch of the user's Hata deposit address; cached
        # for the process lifetime so we sign once, not per page load.
        global _hata_cached
        if _hata_cached is None:
            import hata
            try:
                _hata_cached = hata.get_deposit_address("SOL", "Solana")
            except Exception:
                _hata_cached = {}
        address = (_hata_cached or {}).get("address", "")
        source = "hata"
    if not address:
        return Response(status_code=204)
    import segno
    target = os.environ.get("VERIPAY_WALLET_URL", "").strip() or address
    qr = segno.make(target, error="m").svg_data_uri(scale=4, dark="#e2e2e2", light=None)
    return {"address": address, "url": target, "qr": qr, "source": source}


@app.get("/api/qr")
def share_qr(request: Request, address: str, network: str = "devnet"):
    """Deep link + QR for a wallet audit, so the view is shareable: scan,
    open /?wallet=ADDR on the same network as this server, audit runs on
    load. QR is generated locally from the link we build — never from
    arbitrary caller text."""
    import explorer
    if not explorer._PUBKEY_RE.fullmatch(address or ""):
        return Response("not a valid Solana address", status_code=400)
    if network not in ("devnet", "mainnet"):
        return Response("unknown network", status_code=400)
    import segno
    url = f"{request.base_url}?wallet={address}&network={network}"
    qr = segno.make(url, error="m").svg_data_uri(scale=4, dark="#e2e2e2", light=None)
    return {"url": url, "qr": qr}


@app.get("/api/wallet")
def wallet_activity(address: str, limit: int = 12, network: str = "devnet"):
    """Read-only audit of any address — public chain data, no wallet
    connection, no keys. The demo moment: paste the recipient of our own
    live payment and watch the veripay:paid memo decode straight off RPC
    with zero shared state with this backend."""
    try:
        return explorer.fetch_activity(address, limit=limit, network=network)
    except ValueError as e:
        return Response(str(e), status_code=400)
    except Exception as e:
        return Response(f"RPC error: {e}", status_code=502)


@app.post("/api/build_payment")
async def build_payment(request: Request):
    """Owner-signed path: build an UNSIGNED, gate-checked transaction for
    a release this server produced. The connected wallet signs in
    Phantom; no server key is involved. Engine-less hosts answer 501."""
    body = await request.json()
    root = str(body.get("root", ""))
    payer = str(body.get("payer", ""))
    lamports = int(body.get("lamports", 1000) or 1000)
    result = _released.get(root)
    if result is None:
        return Response("unknown release — this server only builds payments "
                        "for results it verified itself", status_code=404)
    recipient = os.environ.get("VERIPAY_RECIPIENT", "").strip()
    if not recipient:
        return Response("VERIPAY_RECIPIENT not configured", status_code=501)
    try:
        import chain
    except Exception:
        return Response("payment building requires the local engine "
                        "(chain layer is deliberately not deployed here)",
                        status_code=501)
    try:
        tx = chain.build_user_payment(result, payer, recipient, lamports)
    except chain.PaymentBlocked as e:
        return Response(str(e), status_code=409)
    except Exception as e:
        return Response(f"build failed: {e}", status_code=502)
    return {"tx": tx, "recipient": recipient, "lamports": lamports}


@app.post("/api/analyze")
async def analyze(file: UploadFile, question: str = Form(...)):
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".pdf"
    fd, path = tempfile.mkstemp(prefix="finverify_", suffix=suffix)
    with os.fdopen(fd, "wb") as out:
        out.write(await file.read())

    def stream():
        try:
            for event in backend.analyze_stream(path, question):
                if event.get("stage") == "release" and event.get("result"):
                    root = event["result"].get("audit_log_root")
                    if root:
                        _released[root] = event["result"]
                        while len(_released) > 20:
                            _released.pop(next(iter(_released)))
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
    uvicorn.run(app, host=os.environ.get("VERIPAY_HOST", "127.0.0.1"), port=7861)
