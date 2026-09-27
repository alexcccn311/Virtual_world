from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from world_generation.services.loan_service import (
    LenderPolicy,
    execute_player_loan,
    list_player_loan_contracts,
    materialize_player_debt,
    quote_loan,
    settle_due_player_loans,
)


def _database(path: Path, *, cash: int = 50_000) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE organizations(
                organization_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE TABLE characters(
                character_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO organizations VALUES (?, ?, ?)",
            (
                "ORG-LENDER",
                "测试钱庄",
                json.dumps(
                    {"offers_loans": True, "max_total_debt": 1_000_000},
                    ensure_ascii=False,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO characters VALUES (?, ?)",
            (
                "CUS-PLAYER",
                json.dumps(
                    {"cash": cash, "debt": 10_000, "other_assets": 20_000},
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()


def _finances(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as connection:
        raw = connection.execute(
            "SELECT data_json FROM characters WHERE character_id = 'CUS-PLAYER'"
        ).fetchone()[0]
    return json.loads(raw)


def _debt_materialization_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE organizations(
                organization_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data_json TEXT NOT NULL
            );
            CREATE TABLE characters(
                character_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL
            );
            """
        )
        lenders = (
            ("ORG-NEAR", "近处钱庄", [1, 0], 100_000),
            ("ORG-MIDDLE", "中距离钱庄", [4, 0], 100_000),
            ("ORG-UNLIMITED", "远处地下钱庄", [10, 0], None),
        )
        for organization_id, name, address, limit in lenders:
            connection.execute(
                "INSERT INTO organizations VALUES (?, ?, ?)",
                (
                    organization_id,
                    name,
                    json.dumps(
                        {
                            "offers_loans": True,
                            "max_total_debt": limit,
                            "address": address,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        connection.execute(
            "INSERT INTO characters VALUES (?, ?)",
            (
                "CUS-PLAYER",
                json.dumps(
                    {
                        "cash": 12_345,
                        "debt": 250_000,
                        "other_assets": 40_000,
                        "address": [0, 0],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        connection.commit()


class LoanServiceTests(unittest.TestCase):
    def test_initial_debt_is_split_across_nearest_lenders_once(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            _debt_materialization_database(database)
            now = datetime(2026, 9, 27, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            first = materialize_player_debt(
                database,
                "CUS-PLAYER",
                initialized_at=now,
            )
            second = materialize_player_debt(
                database,
                "CUS-PLAYER",
                initialized_at=now + timedelta(days=1),
            )

            self.assertEqual([contract.lender_organization_id for contract in first], [
                "ORG-NEAR", "ORG-MIDDLE", "ORG-UNLIMITED"
            ])
            self.assertEqual([contract.principal for contract in first], [
                100_000, 100_000, 50_000
            ])
            self.assertEqual(sum(contract.principal for contract in first), 250_000)
            self.assertEqual({contract.loan_id for contract in first}, {
                contract.loan_id for contract in second
            })
            for contract in first:
                borrowed_at = datetime.fromisoformat(contract.borrowed_at)
                due_at = datetime.fromisoformat(contract.due_at)
                self.assertEqual(due_at - borrowed_at, timedelta(days=30))
                self.assertGreaterEqual(due_at - now.astimezone(ZoneInfo("UTC")), timedelta(days=15))
                self.assertLessEqual(due_at - now.astimezone(ZoneInfo("UTC")), timedelta(days=30))

            finances = _finances(database)
            expected_debt = sum(contract.total_due for contract in first)
            self.assertEqual(finances["cash"], 12_345)
            self.assertEqual(finances["debt"], expected_debt)
            self.assertEqual(
                finances["net_assets"], 12_345 + 40_000 - expected_debt
            )
            with closing(sqlite3.connect(database)) as connection:
                marker = connection.execute(
                    """
                    SELECT original_debt, initialized_debt
                    FROM player_debt_initializations
                    WHERE borrower_character_id = 'CUS-PLAYER'
                    """
                ).fetchone()
            self.assertEqual(marker, (250_000, expected_debt))

    def test_fee_is_withheld_from_cash_and_not_added_to_repayment(self) -> None:
        quote = quote_loan(
            LenderPolicy("ORG-1", "钱庄", True, None),
            current_debt=0,
            amount=10_000,
        )

        self.assertEqual(quote.fee, 1_000)
        self.assertEqual(quote.cash_disbursed, 9_000)
        self.assertEqual(quote.total_due, quote.principal + quote.interest)
        self.assertNotEqual(quote.total_due, quote.principal + quote.interest + quote.fee)

    def test_limit_only_checks_current_debt_plus_principal(self) -> None:
        lender = LenderPolicy("ORG-1", "钱庄", True, 100_000)
        accepted = quote_loan(lender, current_debt=90_000, amount=10_000)

        self.assertGreater(accepted.total_due + accepted.current_debt, 100_000)
        with self.assertRaisesRegex(ValueError, "超过机构上限"):
            quote_loan(lender, current_debt=90_000, amount=10_001)

    def test_execution_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            _database(database)
            borrowed_at = datetime(2026, 1, 31, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            first = execute_player_loan(
                database,
                "CUS-PLAYER",
                "ORG-LENDER",
                10_000,
                borrowed_at=borrowed_at,
                request_id="request-1",
            )
            second = execute_player_loan(
                database,
                "CUS-PLAYER",
                "ORG-LENDER",
                10_000,
                borrowed_at=borrowed_at,
                request_id="request-1",
            )

            self.assertEqual(first, second)
            self.assertEqual(len(list_player_loan_contracts(database, "CUS-PLAYER")), 1)
            finances = _finances(database)
            self.assertEqual(finances["cash"], 50_000 + first.cash_disbursed)
            self.assertEqual(finances["debt"], 10_000 + first.total_due)
            self.assertEqual(datetime.fromisoformat(first.due_at).day, 28)

    def test_due_contract_is_paid_only_when_cash_is_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            _database(database, cash=50_000)
            borrowed_at = datetime(2026, 3, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            contract = execute_player_loan(
                database,
                "CUS-PLAYER",
                "ORG-LENDER",
                10_000,
                borrowed_at=borrowed_at,
            )
            with closing(sqlite3.connect(database)) as connection:
                paid, overdue = settle_due_player_loans(
                    connection, borrowed_at + timedelta(days=32)
                )
                connection.commit()

            self.assertEqual((paid, overdue), (1, 0))
            updated = list_player_loan_contracts(database, "CUS-PLAYER")[0]
            self.assertEqual(updated.status, "paid")
            self.assertIsNotNone(updated.repaid_at)
            finances = _finances(database)
            self.assertEqual(finances["debt"], 10_000)
            self.assertEqual(
                finances["cash"],
                50_000 + contract.cash_disbursed - contract.total_due,
            )

    def test_insufficient_cash_marks_overdue_without_deduction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / "world.sqlite3"
            _database(database, cash=0)
            borrowed_at = datetime(2026, 4, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            contract = execute_player_loan(
                database,
                "CUS-PLAYER",
                "ORG-LENDER",
                10_000,
                borrowed_at=borrowed_at,
            )
            before = _finances(database)
            with closing(sqlite3.connect(database)) as connection:
                paid, overdue = settle_due_player_loans(
                    connection, borrowed_at + timedelta(days=32)
                )
                connection.commit()

            self.assertEqual((paid, overdue), (0, 1))
            updated = list_player_loan_contracts(database, "CUS-PLAYER")[0]
            self.assertEqual(updated.status, "overdue")
            self.assertEqual(_finances(database), before)
            self.assertGreater(contract.total_due, before["cash"])


if __name__ == "__main__":
    unittest.main()
