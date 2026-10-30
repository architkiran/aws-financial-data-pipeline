"""Storage abstraction: the same pipeline code runs against a local folder or S3.

Keys are always relative POSIX-style paths such as ``raw/dt=2026-10-01/file.csv``.
Writes to a single key are atomic on both backends (rename locally, PUT on S3), which is
what makes the "overwrite one deterministic file per partition" idempotency pattern safe.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd


class Storage(ABC):
    uri: str

    # -- primitives each backend implements ---------------------------------
    @abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def delete_keys(self, keys: Iterable[str]) -> None: ...

    # -- helpers built on the primitives ------------------------------------
    def delete_prefix(self, prefix: str) -> None:
        self.delete_keys(self.list_keys(prefix))

    def write_json(self, obj: Any, key: str) -> None:
        self.put_bytes(key, json.dumps(obj, indent=2, default=str).encode("utf-8"))

    def read_json(self, key: str) -> Any:
        return json.loads(self.get_bytes(key).decode("utf-8"))

    def write_csv(self, df: pd.DataFrame, key: str) -> None:
        self.put_bytes(key, df.to_csv(index=False).encode("utf-8"))

    def read_csv(self, key: str) -> pd.DataFrame:
        # Only truly empty cells are missing. Strings like "N/A" must survive so that
        # validation can report them as invalid rather than silently treating them as null.
        return pd.read_csv(
            io.BytesIO(self.get_bytes(key)), dtype=str, keep_default_na=False, na_values=[""]
        )

    def write_parquet(self, df: pd.DataFrame, key: str, schema: Optional[Any] = None) -> None:
        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
        buffer = io.BytesIO()
        # Athena cannot read nanosecond timestamps, so always store microseconds.
        pq.write_table(
            table,
            buffer,
            compression="snappy",
            coerce_timestamps="us",
            allow_truncated_timestamps=True,
        )
        self.put_bytes(key, buffer.getvalue())

    def read_parquet(self, key: str) -> pd.DataFrame:
        import pyarrow.parquet as pq

        return pq.read_table(io.BytesIO(self.get_bytes(key))).to_pandas()

    def read_parquet_prefix(self, prefix: str) -> Optional[pd.DataFrame]:
        keys = [k for k in self.list_keys(prefix) if k.endswith(".parquet")]
        if not keys:
            return None
        return pd.concat([self.read_parquet(k) for k in keys], ignore_index=True)

    def replace_partition(
        self,
        df: pd.DataFrame,
        prefix: str,
        schema: Optional[Any] = None,
        filename: str = "part-0.parquet",
    ) -> str:
        """Idempotently (re)write a partition.

        The new file is written first under a deterministic name (atomic overwrite), and only
        then are any other files in the partition removed. A reader never sees an empty
        partition, and re-running a day replaces data instead of appending to it.
        """
        if not prefix.endswith("/"):
            prefix += "/"
        key = prefix + filename
        self.write_parquet(df, key, schema)
        stale = [k for k in self.list_keys(prefix) if k != key]
        if stale:
            self.delete_keys(stale)
        return key


class LocalStorage(Storage):
    def __init__(self, root: "str | Path") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.uri = str(self.root)

    def _path(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str = "") -> list[str]:
        keys = []
        for path in self.root.rglob("*"):
            if path.is_file() and not path.name.startswith(".tmp-"):
                key = path.relative_to(self.root).as_posix()
                if key.startswith(prefix):
                    keys.append(key)
        return sorted(keys)

    def delete_keys(self, keys: Iterable[str]) -> None:
        for key in keys:
            self._path(key).unlink(missing_ok=True)


class S3Storage(Storage):
    def __init__(self, bucket: str, prefix: str = "", client: Any = None) -> None:
        import boto3

        clean = prefix.strip("/")
        self.bucket = bucket
        self.prefix = f"{clean}/" if clean else ""
        self.client = client or boto3.client("s3")
        self.uri = f"s3://{bucket}/{self.prefix}"

    def _key(self, key: str) -> str:
        return self.prefix + key

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data)

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
            return True
        except ClientError as err:
            if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list_keys(self, prefix: str = "") -> list[str]:
        keys = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"][len(self.prefix):])
        return sorted(keys)

    def delete_keys(self, keys: Iterable[str]) -> None:
        keys = list(keys)
        for start in range(0, len(keys), 1000):
            chunk = keys[start:start + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": self._key(k)} for k in chunk], "Quiet": True},
            )


def storage_from_uri(uri: str) -> Storage:
    """``s3://bucket/optional/prefix`` -> S3Storage, anything else -> LocalStorage."""
    if uri.startswith("s3://"):
        bucket, _, prefix = uri[len("s3://"):].partition("/")
        return S3Storage(bucket, prefix)
    return LocalStorage(uri)
