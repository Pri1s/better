"""Raw snapshot files: the untouched record of what a provider said.

One file holds one snapshot: every response that made it up, exactly as received,
with the time each was fetched. The database is built from these files and can be
rebuilt from them, so they are written first and never edited.

    data/raw/lines/<source>/<YYYY>/<MM>/<source>_<YYYYMMDDTHHMMSSZ>.json.gz
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

FORMAT = 1
SUBDIR = "lines"
_STAMP = "%Y%m%dT%H%M%SZ"
_FILE = re.compile(r"^(?P<source>[a-z_]+)_(?P<stamp>\d{8}T\d{6}Z)\.json\.gz$")


class RawConflictError(RuntimeError):
    """A different snapshot already exists for the same source and second."""


def utcnow() -> datetime:
    """The current time as a UTC datetime without a zone, the form the database stores."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def parse_time(text: object) -> datetime | None:
    """An ISO 8601 time as UTC without a zone, or ``None`` if it cannot be read.

    Accepts a trailing ``Z`` and any offset. A time without an offset is taken as UTC.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Response:
    """One HTTP exchange (or one file read), with the body exactly as received."""

    url: str
    status: int
    fetched_at_utc: datetime
    body: str


@dataclass(frozen=True)
class Envelope:
    """Everything that makes up one snapshot."""

    source: str
    fetched_at_utc: datetime
    responses: tuple[Response, ...]
    meta: dict = field(default_factory=dict)

    @property
    def stamp(self) -> str:
        return self.fetched_at_utc.strftime(_STAMP)

    @property
    def snapshot_id(self) -> str:
        return f"{self.source}/{self.stamp}"

    @property
    def relative_path(self) -> Path:
        """Where the file lives under the raw directory."""
        moment = self.fetched_at_utc
        return (Path(SUBDIR) / self.source / f"{moment:%Y}" / f"{moment:%m}"
                / f"{self.source}_{self.stamp}.json.gz")

    def to_bytes(self) -> bytes:
        """The file's content. The same envelope always gives the same bytes."""
        document = {
            "format": FORMAT,
            "source": self.source,
            "fetched_at_utc": _iso(self.fetched_at_utc),
            "meta": self.meta,
            "responses": [
                {
                    "url": response.url,
                    "status": response.status,
                    "fetched_at_utc": _iso(response.fetched_at_utc),
                    "body": response.body,
                }
                for response in self.responses
            ],
        }
        text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
        buffer = io.BytesIO()
        # mtime=0 and no file name keep the gzip header free of anything that varies.
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as stream:
            stream.write(text.encode("utf-8"))
        return buffer.getvalue()


def write(envelope: Envelope, raw_root: Path) -> Path:
    """Save an envelope and return its path. Saving the same envelope twice is harmless.

    Raises ``RawConflictError`` if a different snapshot already has that name; two
    captures from one source in the same second mean something is wrong upstream.
    """
    path = raw_root / envelope.relative_path
    content = envelope.to_bytes()
    if path.exists():
        if path.read_bytes() == content or read(path).responses == envelope.responses:
            return path
        raise RawConflictError(f"{path} already holds a different snapshot")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_bytes(content)
    os.replace(partial, path)  # never leave a half-written file where the loader looks
    return path


def read(path: Path) -> Envelope:
    document = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    if document.get("format") != FORMAT:
        raise ValueError(f"{path.name}: unknown raw format {document.get('format')!r}")
    return Envelope(
        source=document["source"],
        fetched_at_utc=_required_time(document["fetched_at_utc"], path),
        meta=document.get("meta") or {},
        responses=tuple(
            Response(
                url=response["url"],
                status=response["status"],
                fetched_at_utc=_required_time(response["fetched_at_utc"], path),
                body=response["body"],
            )
            for response in document["responses"]
        ),
    )


def _required_time(text: str, path: Path) -> datetime:
    moment = parse_time(text)
    if moment is None:
        raise ValueError(f"{path.name}: unreadable time {text!r}")
    return moment


def list_files(raw_root: Path) -> list[Path]:
    """Every snapshot file under the raw directory, oldest first within each source."""
    base = raw_root / SUBDIR
    if not base.exists():
        return []
    return sorted(path for path in base.rglob("*.json.gz") if _FILE.match(path.name))


def snapshot_id_of(path: Path) -> str:
    """The snapshot id a file name stands for, without opening the file."""
    match = _FILE.match(path.name)
    if match is None:
        raise ValueError(f"not a snapshot file: {path.name}")
    return f"{match['source']}/{match['stamp']}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
