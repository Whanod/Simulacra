# Backend Bootstrap

## Install

Install the package with the API and development extras:

```bash
pip install -e '.[api,dev]'
```

This installs the core simulation library, the FastAPI surface in `defi_sim_api`, and the local test dependencies used by the backend test suite.

## Run The API

Use Uvicorn to launch the FastAPI app:

```bash
python -m uvicorn defi_sim_api.main:app --reload
```

The default app entrypoint is `defi_sim_api.main:app`.

## Smoke Check

After the server starts, verify the health endpoint:

```bash
curl http://127.0.0.1:8000/health
```

## Backend Test Commands

```bash
pytest tests/api
pytest tests/engine tests/report tests/validation tests/test_review_regressions.py
```
