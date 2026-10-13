import sqlite3

class Database:
    def __init__(self, db_path: str = "tasks.db"):
        self.db_path = db_path

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def init_db(self, schema_path: str = "schema.sql"):
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = f.read()
        with self.get_connection() as conn:
            conn.executescript(schema)
