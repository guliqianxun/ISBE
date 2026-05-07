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
) -> UUID:
    artifact_id = uuid4()
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

    # Local mirror — convenience for human inspection without mc
    mirror_root = Path(os.getenv("ISBE_ARTIFACT_MIRROR", str(LOCAL_MIRROR_DEFAULT)))
    local_path = mirror_root / topic_id / period_label / f"{artifact_id}.md"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(body_bytes)

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
