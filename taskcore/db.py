"""Подключение к SQLite, транзакции и миграции."""
import contextlib
import os
import sqlite3

from .migrations import MIGRATIONS


def migrate(conn):
    """Применяет недостающие миграции. Каждая — в своей транзакции."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        try:
            conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {version};\nCOMMIT;")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return conn.execute("PRAGMA user_version").fetchone()[0]


class Database:
    def __init__(self, path, timeout=5.0):
        self.path = str(path)
        if self.path != ":memory:":
            folder = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(folder, exist_ok=True)
        # isolation_level=None: транзакциями управляем сами (BEGIN IMMEDIATE).
        self.conn = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = FULL")
        self._depth = 0
        self.schema_version = migrate(self.conn)

    @contextlib.contextmanager
    def tx(self):
        """Атомарная транзакция. Вложенные вызовы присоединяются к внешней.

        BEGIN IMMEDIATE сразу берёт блокировку записи, поэтому два процесса
        не могут одновременно изменить одну и ту же задачу.
        """
        if self._depth:
            self._depth += 1
            try:
                yield self.conn
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise
        finally:
            self._depth = 0

    def close(self):
        self.conn.close()
