# QRIS Guard

An AI agent that checks a QRIS payment QR code *before* you pay, and returns a verdict:
**Safe (no red flags) / Caution / Do not pay**, with reasons.

Built for the Google Cloud AI Builder Cup 2026 (Theme: BFSI, Intelligent Risk, Fraud & Financial Experiences).

> Status: scaffold. Deploy pipeline only. See SPEC.md for the full MVP.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

## Deploy to Cloud Run

```bash
gcloud run deploy qris-guard \
  --source . \
  --region asia-southeast2 \
  --allow-unauthenticated
```

All demo data in this repository is synthetic.
