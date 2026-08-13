"""Private document storage adapters."""

from pathlib import Path
import uuid


class LocalPrivateStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, suffix: str) -> str:
        key = f"{uuid.uuid4().hex}{suffix}"
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Invalid storage path.")
        target.write_bytes(content)
        return key

    def delete(self, key: str) -> None:
        target = (self.root / key).resolve()
        if self.root in target.parents:
            target.unlink(missing_ok=True)


class S3PrivateStorage:
    def __init__(self, bucket: str, prefix: str):
        if not bucket:
            raise ValueError("S3_BUCKET is required for S3 storage.")
        import boto3
        self.client = boto3.client("s3")
        self.bucket, self.prefix = bucket, prefix.strip("/")

    def put(self, content: bytes, suffix: str) -> str:
        key = f"{self.prefix}/{uuid.uuid4().hex}{suffix}"
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=content,
            ServerSideEncryption="AES256", ContentType="application/octet-stream",
        )
        return key

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def create_storage(settings):
    if settings.upload_storage_backend.casefold() == "s3":
        return S3PrivateStorage(settings.s3_bucket, settings.s3_prefix)
    return LocalPrivateStorage(settings.secure_upload_dir)
