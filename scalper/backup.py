from contextlib import closing
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
import re
import sqlite3


def copy_database(source: Path, destination: Path) -> None:
    deadline = monotonic() + 20

    def check_timeout(*_):
        if monotonic() > deadline:
            raise sqlite3.OperationalError("Backup melewati batas waktu.")

    source_uri = source.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as db, closing(sqlite3.connect(destination)) as copy:
        db.backup(copy, pages=256, progress=check_timeout, sleep=0.1)
        if copy.execute("PRAGMA quick_check(1)").fetchone() != ("ok",):
            raise sqlite3.DatabaseError("Pemeriksaan integritas backup gagal.")


def daily_copies(database: Path) -> list[Path]:
    directory = database.parent / "backups"
    pattern = re.compile(re.escape(database.name) + r"\.auto-\d{4}-\d{2}-\d{2}\.sqlite3$")
    return sorted(
        (path for path in directory.iterdir() if pattern.fullmatch(path.name) and path.is_file()),
        reverse=True,
    ) if directory.is_dir() else []


def save_daily_copy(database: Path, now: datetime) -> None:
    directory = database.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{database.name}.auto-{now.date().isoformat()}.sqlite3"
    if not destination.exists():
        with NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            copy_database(database, temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    for old in daily_copies(database)[7:]:
        old.unlink(missing_ok=True)
