import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import uuid4


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS ejections (
                    order_id TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL,
                    powerbank_id TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS payments (
                    order_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (order_id, kind)
                );
            """)

    def eject(self, order_id: str, station_id: str, available: bool) -> dict:
        powerbank_id = f"pb-{uuid4().hex}" if available else None
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """INSERT INTO ejections VALUES (?, ?, ?)
                   ON CONFLICT (order_id) DO NOTHING""",
                (order_id, station_id, powerbank_id),
            )
            (stored_id,) = db.execute(
                "SELECT powerbank_id FROM ejections WHERE order_id = ?", (order_id,)
            ).fetchone()
        return {"success": stored_id is not None, "powerbank_id": stored_id}

    def pay(self, kind: str, order_id: str, user_id: str, amount: int) -> dict:
        status = "declined" if kind == "HOLD" and user_id.startswith("poor-") else "success"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                """INSERT INTO payments (order_id, kind, user_id, amount, status)
                   VALUES (:order_id, :kind, :user_id, :amount, CASE
                       WHEN :kind = 'HOLD' AND EXISTS (
                           SELECT 1 FROM payments
                           WHERE order_id = :order_id AND kind = 'CLEAR' AND status = 'success'
                       ) THEN 'declined'
                       ELSE :status END)
                   ON CONFLICT (order_id, kind) DO NOTHING""",
                {"order_id": order_id, "kind": kind, "user_id": user_id, "amount": amount, "status": status},
            )
            (stored_status,) = db.execute(
                "SELECT status FROM payments WHERE order_id = ? AND kind = ?", (order_id, kind)
            ).fetchone()
        return {"status": stored_status}

    def summary(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            count, amount = db.execute(
                """SELECT count(*), coalesce(sum(amount), 0)
                   FROM payments WHERE kind = 'CLEAR' AND status = 'success'"""
            ).fetchone()
        return {"clear_count": count, "clear_amount": amount}

    def ejections(self) -> list[dict]:
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute(
                """SELECT order_id, station_id, powerbank_id FROM ejections
                   WHERE powerbank_id IS NOT NULL ORDER BY rowid"""
            )]
