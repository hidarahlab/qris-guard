from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="QRIS Guard")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!doctype html>
    <html lang="en">
      <head><meta charset="utf-8"><title>QRIS Guard</title></head>
      <body style="font-family: sans-serif; max-width: 640px; margin: 4rem auto;">
        <h1>QRIS Guard</h1>
        <p>Hello from Cloud Run. Deploy pipeline works.</p>
      </body>
    </html>
    """
