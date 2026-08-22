"""Capture README screenshots from the live app with system Chrome.
DEMO_FALLBACK=1 for a deterministic, offline pipeline run."""
import os, subprocess, sys, time

os.environ["DEMO_FALLBACK"] = "1"
for k in ("DEEPSEEK_API_KEY", "deepseek_api"):
    os.environ.pop(k, None)

srv = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "server:app", "--port", "7871"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=os.environ)
try:
    import httpx
    for _ in range(40):
        try:
            httpx.get("http://127.0.0.1:7871/", timeout=2); break
        except Exception:
            time.sleep(0.5)

    from playwright.sync_api import sync_playwright
    out = os.path.join("docs", "screenshots")
    with sync_playwright() as p:
        page = p.chromium.launch(channel="chrome").new_page(
            viewport={"width": 1600, "height": 1000})
        page.goto("http://127.0.0.1:7871/")
        page.wait_for_timeout(800)
        page.screenshot(path=f"{out}/idle.png")
        page.set_input_files("#file", "sample_report.pdf")
        page.click("#exec")
        page.wait_for_selector(".flow .card", timeout=30000)
        page.wait_for_timeout(1600)  # let entrance animations settle
        page.screenshot(path=f"{out}/verified_run.png", full_page=True)
        print("captured: idle.png, verified_run.png")

        if os.environ.get("SHOT_WALLET"):  # VeriPay only
            page.fill("#waddr", os.environ["SHOT_WALLET"])
            page.click("#wgo")
            page.wait_for_selector(".flow .card", timeout=30000)
            page.wait_for_timeout(1600)
            page.screenshot(path=f"{out}/wallet_audit.png", full_page=True)
            print("captured: wallet_audit.png")
finally:
    srv.terminate()
