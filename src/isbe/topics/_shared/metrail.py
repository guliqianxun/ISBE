"""HTTP client for metrail-web — the LAN PDF-extraction service.

metrail-web accepts PDFs only (POST /api/documents, multipart; anything else
422s), processes them asynchronously in a small thread pool, and serves
results as atoms or a concatenated `corpus` markdown export. There is no
webhook — clients poll GET /api/documents/{id} until state is done/error.

Designed-around caveats:
- A service restart loses in-flight jobs: they stay "processing" forever and
  POST /process 409s. wait_until_done() therefore enforces a hard deadline
  (MetrailTimeout); callers skip the document and retry next run. A re-upload
  creates a fresh doc id — wasteful for a permanently stuck PDF, but correct.
- The service has no auth: the base URL must only ever point inside the LAN.

Env: METRAIL_API_URL (e.g. http://192.168.0.42:8000). Unset = disabled.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from isbe.http_retry import HTTP_RETRY


class MetrailError(Exception):
    """Non-retryable client/service error (e.g. rejected upload)."""


class MetrailJobFailed(MetrailError):
    """The service processed the document and reported state=error."""


class MetrailTimeout(MetrailError):
    """The job did not reach done/error within the polling deadline."""


def metrail_base_url(override: str | None = None) -> str | None:
    """Resolve the service base URL: explicit override → env → None (disabled)."""
    base = (override or os.getenv("METRAIL_API_URL", "")).strip()
    return base.rstrip("/") or None


@HTTP_RETRY
def submit_pdf(
    pdf_path: Path,
    *,
    base_url: str,
    backend: str = "pdfplumber",
    ocr: bool = False,
    timeout_s: float = 60.0,
) -> str:
    """Upload one PDF; returns the service-side document id (202 Accepted)."""
    with pdf_path.open("rb") as f:
        resp = httpx.post(
            f"{base_url}/api/documents",
            files={"file": (pdf_path.name, f, "application/pdf")},
            data={"backend": backend, "ocr": str(ocr).lower()},
            timeout=timeout_s,
        )
    if resp.status_code == 422:
        raise MetrailError(f"metrail rejected {pdf_path.name}: {resp.text[:200]}")
    resp.raise_for_status()
    doc_id = _as_dict(resp, context=pdf_path.name).get("id")
    if not doc_id:
        raise MetrailError(f"metrail returned no id for {pdf_path.name}")
    return doc_id


def _as_dict(resp: httpx.Response, *, context: str) -> dict:
    """Parse a JSON object body; anything else (proxy interstitial, half-up
    uvicorn returning HTML with a 200) becomes MetrailError, not a stray
    ValueError/AttributeError escaping the per-paper handler."""
    try:
        data = resp.json()
    except ValueError as e:
        raise MetrailError(f"metrail returned non-JSON for {context}: {resp.text[:120]}") from e
    if not isinstance(data, dict):
        raise MetrailError(f"metrail returned non-object JSON for {context}")
    return data


@HTTP_RETRY
def _get_doc(doc_id: str, *, base_url: str, timeout_s: float = 30.0) -> dict:
    resp = httpx.get(f"{base_url}/api/documents/{doc_id}", timeout=timeout_s)
    resp.raise_for_status()
    return _as_dict(resp, context=doc_id)


def wait_until_done(
    doc_id: str,
    *,
    base_url: str,
    poll_interval_s: float = 3.0,
    timeout_s: float = 120.0,
    sleep=time.sleep,
) -> dict:
    """Poll until the job reaches done (returns meta) or error (raises).

    The hard deadline guards against restart-orphaned jobs that stay
    "processing" forever. `sleep` is a test seam.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        meta = _get_doc(doc_id, base_url=base_url)
        state = meta.get("state")
        if state == "done":
            return meta
        if state == "error":
            raise MetrailJobFailed(f"metrail job {doc_id} failed: {meta.get('error')}")
        if time.monotonic() >= deadline:
            raise MetrailTimeout(f"metrail job {doc_id} still {state!r} after {timeout_s:.0f}s")
        sleep(poll_interval_s)


@HTTP_RETRY
def fetch_figure_atoms(doc_id: str, *, base_url: str, timeout_s: float = 30.0) -> list[dict]:
    """List the document's figure atoms: {id, page, text (caption), image_url, ...}."""
    resp = httpx.get(
        f"{base_url}/api/documents/{doc_id}/atoms",
        params={"kind": "figure"},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


@HTTP_RETRY
def download_asset(image_url: str, *, base_url: str, timeout_s: float = 60.0) -> bytes:
    """Download an atom asset; image_url from the API is service-relative."""
    url = image_url if image_url.startswith("http") else f"{base_url}{image_url}"
    resp = httpx.get(url, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


@HTTP_RETRY
def fetch_corpus_markdown(doc_id: str, *, base_url: str, timeout_s: float = 60.0) -> str:
    """Fetch the whole-document markdown export (## [kind] page N · id sections)."""
    resp = httpx.get(
        f"{base_url}/api/documents/{doc_id}/export",
        params={"format": "corpus"},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return resp.text


def extract_pdf_to_markdown(
    pdf_path: Path,
    *,
    base_url: str,
    poll_timeout_s: float = 120.0,
    backend: str = "pdfplumber",
    ocr: bool = False,
) -> str:
    """Upload → poll → fetch corpus markdown, end to end for one PDF."""
    doc_id = submit_pdf(pdf_path, base_url=base_url, backend=backend, ocr=ocr)
    wait_until_done(doc_id, base_url=base_url, timeout_s=poll_timeout_s)
    return fetch_corpus_markdown(doc_id, base_url=base_url)
