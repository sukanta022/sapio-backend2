
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.object_storage import (
    s3_client,
    bucket_name,
    upload_path,
    download_path,
    delete_object,
)


def main():
    key = f"health-checks/{uuid.uuid4().hex}.txt"

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source.txt"
        downloaded = root / "downloaded.txt"

        source.write_text(
            "Sapio AI object storage verification",
            encoding="utf-8",
        )

        print("Checking bucket access...")

        s3_client().head_bucket(
            Bucket=bucket_name()
        )

        print("Uploading test file...")

        try:
            upload_path(
                source,
                key,
                "text/plain",
            )

            print("Downloading test file...")

            download_path(
                key,
                downloaded,
            )

            assert (
                downloaded.read_bytes() == source.read_bytes()
            ), "Downloaded content mismatch"

            print("S3 storage test: SUCCESS")

        finally:
            print("Deleting temporary test object...")
            delete_object(key)


if __name__ == "__main__":
    main()
