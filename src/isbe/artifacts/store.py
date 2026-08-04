import io
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from minio import Minio

from isbe.facts.artifacts import Artifact
from isbe.facts.db import make_session_factory

ARTIFACT_BUCKET = "isbe-artifacts"
LOCAL_MIRROR_DEFAULT = Path("artifacts")


@lru_cache(maxsize=1)
def _get_minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", "isbe"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "changeme123"),
        secure=False,
    )


def _ensure_bucket(client: Minio) -> None:
    if not client.bucket_exists(ARTIFACT_BUCKET):
        client.make_bucket(ARTIFACT_BUCKET)


def save_artifact(
    *,
    topic_id: str,
    kind: str,
    period_label: str,
    body_markdown: str,
    fingerprint: dict,
    generated_at: datetime,
    artifact_id: UUID | None = None,
) -> UUID:
    # Caller may precompute the id so the rendered body can embed it (the
    # audit block used to ship a literal "(filled below)" placeholder).
    artifact_id = artifact_id or uuid4()
    body_bytes = body_markdown.encode("utf-8")
    object_name = f"{topic_id}/{period_label}/{artifact_id}.md"

    client = _get_minio_client()
    _ensure_bucket(client)
    client.put_object(
        ARTIFACT_BUCKET,
        object_name,
        data=io.BytesIO(body_bytes),
        length=len(body_bytes),
        content_type="text/markdown; charset=utf-8",
    )

    # Local mirror — convenience for human inspection without mc.
    # Filenames are now `<period>-<short>.md` (human readable); a `latest.md`
    # copy always points at the freshest render; older renders for the same
    # (topic, period) are rotated into `.history/` so each period dir has
    # exactly one current artifact + one latest.md at top level.
    mirror_root = Path(os.getenv("ISBE_ARTIFACT_MIRROR", str(LOCAL_MIRROR_DEFAULT)))
    period_dir = mirror_root / topic_id / period_label
    period_dir.mkdir(parents=True, exist_ok=True)

    history_dir = period_dir / ".history"
    for existing in period_dir.glob("*.md"):
        if existing.name == "latest.md":
            continue
        history_dir.mkdir(exist_ok=True)
        existing.rename(history_dir / existing.name)

    short = artifact_id.hex[:8]
    local_path = period_dir / f"{period_label}-{short}.md"
    local_path.write_bytes(body_bytes)
    (period_dir / "latest.md").write_bytes(body_bytes)

    Session = make_session_factory()
    with Session() as s:
        s.add(
            Artifact(
                id=artifact_id,
                topic_id=topic_id,
                kind=kind,
                period_label=period_label,
                body_uri=f"minio://{ARTIFACT_BUCKET}/{object_name}",
                fingerprint=fingerprint,
                created_at=generated_at,
            )
        )
        s.commit()
    return artifact_id
