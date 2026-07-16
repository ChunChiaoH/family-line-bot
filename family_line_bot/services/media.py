import logging

logger = logging.getLogger(__name__)


class MediaStore:
    """GCS-backed persistence for media bytes (photos, video thumbnails).

    Firestore's 1MB doc limit rules out inline storage, so bytes go to a
    bucket and the message doc keeps only the blob path. Failures are
    logged and swallowed — losing one upload must never break message
    handling (the in-memory cache still covers the common quoted-question
    case within the same instance).
    """

    def __init__(self, bucket: str, project: str = ""):
        # Lazy import so google-cloud-storage isn't required for local dev.
        from google.cloud import storage

        client = storage.Client(project=project) if project else storage.Client()
        self._bucket = client.bucket(bucket)

    def save(
        self, chat_id: str, message_id: str, data: bytes, content_type: str = "image/jpeg"
    ) -> str | None:
        path = f"chats/{chat_id}/{message_id}.jpg"
        try:
            self._bucket.blob(path).upload_from_string(data, content_type=content_type)
            return path
        except Exception:
            logger.error("Failed to persist media to %s", path, exc_info=True)
            return None

    def load(self, path: str) -> bytes | None:
        try:
            return self._bucket.blob(path).download_as_bytes()
        except Exception:
            logger.warning("Failed to load media %s", path, exc_info=True)
            return None
