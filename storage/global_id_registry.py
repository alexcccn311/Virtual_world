"""Cross-database ID allocation for generated worlds."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


GLOBAL_ID_REGISTRY_FILENAME = "global_id_registry.sqlite3"
CHARACTER_ID_PREFIX = "CUS"
CHARACTER_ID_WIDTH = 8


_SCHEMA = """
CREATE TABLE IF NOT EXISTS global_sequences (
    kind TEXT PRIMARY KEY,
    next_value INTEGER NOT NULL CHECK (next_value >= 1)
);

CREATE TABLE IF NOT EXISTS allocation_blocks (
    allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    first_value INTEGER NOT NULL,
    next_value INTEGER NOT NULL,
    owner_database TEXT NOT NULL,
    allocated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claimed_non_numeric_ids (
    kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    owner_database TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY(kind, entity_id)
);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_character_id(number: int) -> str:
    """Format a positive global character sequence as its permanent ID."""

    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        raise ValueError("角色序号必须是大于 0 的整数")
    return f"{CHARACTER_ID_PREFIX}-{number:0{CHARACTER_ID_WIDTH}d}"


def _numeric_character_id(entity_id: str) -> int | None:
    prefix = f"{CHARACTER_ID_PREFIX}-"
    suffix = entity_id[len(prefix) :] if entity_id.startswith(prefix) else ""
    return int(suffix) if suffix.isdigit() else None


class GlobalCharacterIdRegistry:
    """Reserve character IDs shared by every world DB in one data folder."""

    def __init__(
        self,
        registry_path: str | Path,
        *,
        world_database_directory: str | Path | None = None,
    ) -> None:
        self.registry_path = Path(registry_path).resolve()
        self.world_database_directory = Path(
            world_database_directory or self.registry_path.parent
        ).resolve()
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(_SCHEMA)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO global_sequences(kind, next_value)
                    VALUES ('character', 1)
                    """
                )
        self.reconcile_existing_worlds()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.registry_path, timeout=30.0)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _known_next_value(self, database: Path) -> int:
        try:
            connection = sqlite3.connect(
                f"{database.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=2.0,
            )
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if "characters" not in tables:
                    return 1
                if "id_sequences" in tables:
                    row = connection.execute(
                        """
                        SELECT next_value FROM id_sequences
                        WHERE kind = 'character'
                        """
                    ).fetchone()
                    if row is not None:
                        return max(1, int(row[0]))
                row = connection.execute(
                    """
                    SELECT character_id FROM characters
                    WHERE character_id GLOB 'CUS-[0-9]*'
                    ORDER BY character_id DESC LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return 1
                number = _numeric_character_id(str(row[0]))
                return 1 if number is None else number + 1
            finally:
                connection.close()
        except (OSError, sqlite3.Error, ValueError):
            return 1

    def reconcile_existing_worlds(self) -> int:
        """Advance past every range already reserved by sibling world DBs."""

        highest_next = 1
        if self.world_database_directory.is_dir():
            for database in self.world_database_directory.glob("*.sqlite3"):
                if database.resolve() == self.registry_path:
                    continue
                highest_next = max(
                    highest_next,
                    self._known_next_value(database),
                )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE global_sequences
                SET next_value = MAX(next_value, ?)
                WHERE kind = 'character'
                """,
                (highest_next,),
            )
            next_value = int(
                connection.execute(
                    """
                    SELECT next_value FROM global_sequences
                    WHERE kind = 'character'
                    """
                ).fetchone()[0]
            )
            connection.commit()
            return next_value
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve(self, count: int, *, owner_database: str) -> list[str]:
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError("character ID 预留数量必须是正整数")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            start = int(
                connection.execute(
                    """
                    SELECT next_value FROM global_sequences
                    WHERE kind = 'character'
                    """
                ).fetchone()[0]
            )
            next_value = start + count
            connection.execute(
                """
                UPDATE global_sequences SET next_value = ?
                WHERE kind = 'character'
                """,
                (next_value,),
            )
            connection.execute(
                """
                INSERT INTO allocation_blocks(
                    kind, first_value, next_value,
                    owner_database, allocated_at
                ) VALUES ('character', ?, ?, ?, ?)
                """,
                (start, next_value, owner_database, _utc_now()),
            )
            connection.commit()
            return [
                format_character_id(number)
                for number in range(start, next_value)
            ]
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim(self, entity_id: str, *, owner_database: str) -> None:
        number = _numeric_character_id(entity_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if number is not None:
                next_value = int(
                    connection.execute(
                        """
                        SELECT next_value FROM global_sequences
                        WHERE kind = 'character'
                        """
                    ).fetchone()[0]
                )
                if number < next_value:
                    raise ValueError(f"Character ID“{entity_id}”已被全局占用")
                connection.execute(
                    """
                    UPDATE global_sequences SET next_value = ?
                    WHERE kind = 'character'
                    """,
                    (number + 1,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO claimed_non_numeric_ids(
                        kind, entity_id, owner_database, claimed_at
                    ) VALUES ('character', ?, ?, ?)
                    """,
                    (entity_id, owner_database, _utc_now()),
                )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            raise ValueError(f"Character ID“{entity_id}”已被全局占用") from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def next_value(self) -> int:
        with closing(self._connect()) as connection:
            return int(
                connection.execute(
                    """
                    SELECT next_value FROM global_sequences
                    WHERE kind = 'character'
                    """
                ).fetchone()[0]
            )


__all__ = [
    "GLOBAL_ID_REGISTRY_FILENAME",
    "GlobalCharacterIdRegistry",
    "format_character_id",
]
