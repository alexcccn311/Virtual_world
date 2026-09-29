"""Shared cash-only payments; caller-owned transactions keep purchases atomic."""
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path


def can_afford(cash, amount):
    if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount < 0:
        raise ValueError("消费金额必须是有限的非负数")
    return (not isinstance(cash, bool) and isinstance(cash, (int, float))
            and math.isfinite(cash) and cash >= amount)


def spend_cash(database, character_id, amount):
    """Return success/fail. Insufficient funds change nothing, never create debt.

    Pass a path for a committed payment, or an active SQLite transaction to
    combine payment with delivery. No commits are made on a supplied connection.
    """
    can_afford(0, amount)
    if not isinstance(database, sqlite3.Connection):
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True, timeout=30)) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            return spend_cash(conn, character_id, amount)
    if not database.in_transaction:
        raise ValueError("扣款必须在事务内执行")
    row = database.execute("SELECT data_json FROM characters WHERE character_id=?", (character_id,)).fetchone()
    if row is None:
        raise LookupError("角色不存在")
    payload = json.loads(row[0])
    if not can_afford(payload.get("cash", 0), amount):
        return "fail"
    cash = payload.get("cash", 0) - amount
    net_assets = cash + payload.get("other_assets", 0) - payload.get("debt", 0)
    database.execute(
        "UPDATE characters SET data_json=json_set(data_json,'$.cash',?,'$.net_assets',?) WHERE character_id=?",
        (cash, net_assets, character_id),
    )
    return "success"
