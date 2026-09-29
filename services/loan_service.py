"""Loan quotation, player-contract persistence, and due-date settlement."""
from __future__ import annotations

import calendar
import json
import random
import sqlite3
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from .. import config
from .cash_service import spend_cash
from ..models.entities import LoanQuote, PlayerLoanContract


DEFAULT_TIMEZONE = "Asia/Shanghai"

LOAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS player_loan_contracts (
    loan_id TEXT PRIMARY KEY,
    borrower_character_id TEXT NOT NULL,
    lender_organization_id TEXT NOT NULL,
    lender_organization_name TEXT NOT NULL,
    borrowed_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    principal INTEGER NOT NULL CHECK (principal > 0),
    cash_disbursed INTEGER NOT NULL CHECK (cash_disbursed >= 0),
    monthly_rate_bps INTEGER NOT NULL CHECK (monthly_rate_bps >= 0),
    interest INTEGER NOT NULL CHECK (interest >= 0),
    fee_bps INTEGER NOT NULL CHECK (fee_bps >= 0),
    fee INTEGER NOT NULL CHECK (fee >= 0),
    total_due INTEGER NOT NULL CHECK (total_due > 0),
    status TEXT NOT NULL CHECK (status IN ('active', 'paid', 'overdue')),
    repaid_at TEXT,
    request_id TEXT UNIQUE,
    FOREIGN KEY (borrower_character_id) REFERENCES characters(character_id),
    FOREIGN KEY (lender_organization_id) REFERENCES organizations(organization_id)
);

CREATE INDEX IF NOT EXISTS idx_player_loan_contracts_borrower
    ON player_loan_contracts(borrower_character_id, borrowed_at);
CREATE INDEX IF NOT EXISTS idx_player_loan_contracts_due
    ON player_loan_contracts(status, due_at);

CREATE TABLE IF NOT EXISTS player_debt_initializations (
    borrower_character_id TEXT PRIMARY KEY,
    original_debt INTEGER NOT NULL CHECK (original_debt >= 0),
    initialized_debt INTEGER NOT NULL CHECK (initialized_debt >= 0),
    initialized_at TEXT NOT NULL,
    FOREIGN KEY (borrower_character_id) REFERENCES characters(character_id)
);
"""


class LendingOrganization(Protocol):
    id: str
    offers_loans: bool
    max_total_debt: int | None


@dataclass(slots=True, frozen=True)
class LenderPolicy:
    id: str
    name: str
    offers_loans: bool
    max_total_debt: int | None


def ensure_loan_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(LOAN_SCHEMA)


def lending_blackness(organization: LendingOrganization) -> float:
    """Return a lender's monotonic 0..1 harshness derived from its debt limit."""
    if not organization.offers_loans:
        raise ValueError(f"组织 {organization.id} 不提供贷款")
    if organization.max_total_debt is None:
        return 1.0
    return organization.max_total_debt / (
        organization.max_total_debt + config.LOAN_DEBT_SCALE
    )


def quote_loan(
    organization: LendingOrganization,
    current_debt: int,
    amount: int,
) -> LoanQuote:
    """Quote a one-month loan whose fee is withheld from its cash payout."""
    if isinstance(current_debt, bool) or not isinstance(current_debt, int) or current_debt < 0:
        raise ValueError("current_debt 必须是非负整数")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        raise ValueError("amount 必须是大于 0 的整数")
    if not organization.offers_loans:
        raise ValueError(f"组织 {organization.id} 不提供贷款")
    total_debt = current_debt + amount
    if organization.max_total_debt is not None and total_debt > organization.max_total_debt:
        raise ValueError(
            f"贷款后总负债 ¥{total_debt:,} 超过机构上限 ¥{organization.max_total_debt:,}"
        )
    blackness = lending_blackness(organization)
    debt_pressure = total_debt / (total_debt + config.LOAN_DEBT_SCALE)
    monthly_rate_bps = round(
        config.LOAN_BASE_MONTHLY_RATE_BPS
        + config.LOAN_BLACKNESS_RATE_SPREAD_BPS * blackness
        + config.LOAN_DEBT_RATE_SPREAD_BPS * debt_pressure
    )
    fee_bps = round(
        config.LOAN_BASE_FEE_BPS
        + config.LOAN_BLACKNESS_FEE_SPREAD_BPS * blackness
    )
    interest = round(amount * monthly_rate_bps / 10_000)
    fee = round(amount * fee_bps / 10_000)
    return LoanQuote(
        organization_id=organization.id,
        current_debt=current_debt,
        principal=amount,
        blackness=blackness,
        debt_pressure=debt_pressure,
        monthly_rate_bps=monthly_rate_bps,
        fee_bps=fee_bps,
        interest=interest,
        fee=fee,
        cash_disbursed=max(0, amount - fee),
        total_due=amount + interest,
    )


def _add_calendar_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _aware_time(value: datetime | None, timezone_name: str) -> datetime:
    timezone = ZoneInfo(timezone_name)
    resolved = value or datetime.now(timezone)
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise ValueError("时间必须是带时区的 datetime")
    return resolved.astimezone(timezone)


def _load_lender_policy(
    connection: sqlite3.Connection,
    organization_id: str,
) -> LenderPolicy:
    row = connection.execute(
        "SELECT name, data_json FROM organizations WHERE organization_id = ?",
        (organization_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"贷款机构不存在：{organization_id}")
    try:
        payload = json.loads(row[1])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"贷款机构 {organization_id} 的数据无效") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("offers_loans"), bool):
        raise ValueError(
            f"贷款机构 {organization_id} 缺少生成期放贷配置；请迁移或重新生成该世界"
        )
    raw_limit = payload.get("max_total_debt")
    if raw_limit is not None and (
        isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0
    ):
        raise ValueError(f"贷款机构 {organization_id} 的贷款上限无效")
    return LenderPolicy(
        id=organization_id,
        name=str(row[0]),
        offers_loans=payload["offers_loans"],
        max_total_debt=raw_limit,
    )


def _load_character_finances(
    connection: sqlite3.Connection,
    character_id: str,
) -> tuple[dict[str, object], int, int, int]:
    row = connection.execute(
        "SELECT data_json FROM characters WHERE character_id = ?",
        (character_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"借款角色不存在：{character_id}")
    try:
        payload = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(f"借款角色 {character_id} 的数据无效") from error
    if not isinstance(payload, dict):
        raise ValueError(f"借款角色 {character_id} 的数据必须是对象")
    values: list[int] = []
    for field in ("cash", "debt", "other_assets"):
        value = payload.get(field, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"借款角色 {character_id} 的 {field} 无效")
        values.append(int(value))
    return payload, values[0], values[1], values[2]


def _contract_from_row(row: sqlite3.Row) -> PlayerLoanContract:
    return PlayerLoanContract(
        loan_id=str(row["loan_id"]),
        borrower_character_id=str(row["borrower_character_id"]),
        lender_organization_id=str(row["lender_organization_id"]),
        lender_organization_name=str(row["lender_organization_name"]),
        borrowed_at=str(row["borrowed_at"]),
        due_at=str(row["due_at"]),
        principal=int(row["principal"]),
        cash_disbursed=int(row["cash_disbursed"]),
        monthly_rate_bps=int(row["monthly_rate_bps"]),
        interest=int(row["interest"]),
        fee_bps=int(row["fee_bps"]),
        fee=int(row["fee"]),
        total_due=int(row["total_due"]),
        status=str(row["status"]),
        repaid_at=str(row["repaid_at"]) if row["repaid_at"] else None,
    )


def _contract_by_request_id(
    connection: sqlite3.Connection,
    request_id: str,
) -> PlayerLoanContract | None:
    row = connection.execute(
        "SELECT * FROM player_loan_contracts WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return _contract_from_row(row) if row is not None else None


def _current_lender_principal(
    connection: sqlite3.Connection,
    borrower_character_id: str,
    lender_organization_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(principal), 0)
        FROM player_loan_contracts
        WHERE borrower_character_id = ?
          AND lender_organization_id = ?
          AND status IN ('active', 'overdue')
        """,
        (borrower_character_id, lender_organization_id),
    ).fetchone()
    return int(row[0])


def _coordinate(value: object, *, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(
            isinstance(component, bool) or not isinstance(component, int)
            for component in value
        )
    ):
        raise ValueError(f"{label}缺少有效的二元坐标 address")
    return int(value[0]), int(value[1])


def _available_lenders_by_distance(
    connection: sqlite3.Connection,
    borrower_address: tuple[int, int],
) -> list[tuple[int, LenderPolicy]]:
    lenders: list[tuple[int, LenderPolicy]] = []
    for organization_id, name, raw_payload in connection.execute(
        "SELECT organization_id, name, data_json FROM organizations"
    ):
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("offers_loans") is not True:
            continue
        raw_limit = payload.get("max_total_debt")
        if raw_limit is not None and (
            isinstance(raw_limit, bool)
            or not isinstance(raw_limit, int)
            or raw_limit <= 0
        ):
            raise ValueError(f"贷款机构 {organization_id} 的贷款上限无效")
        lender_address = _coordinate(
            payload.get("address"), label=f"贷款机构 {organization_id}"
        )
        dq = borrower_address[0] - lender_address[0]
        dr = borrower_address[1] - lender_address[1]
        distance_cells = (abs(dq) + abs(dr) + abs(dq + dr)) // 2
        lenders.append(
            (
                distance_cells,
                LenderPolicy(
                    id=str(organization_id),
                    name=str(name),
                    offers_loans=True,
                    max_total_debt=raw_limit,
                ),
            )
        )
    lenders.sort(key=lambda item: (item[0], item[1].id))
    return lenders


def materialize_player_debt(
    database_path: str | Path,
    borrower_character_id: str,
    *,
    initialized_at: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> tuple[PlayerLoanContract, ...]:
    """Turn a generated player's aggregate debt into nearby lender contracts once.

    The generated cash already represents the character's present balance, so
    historical payouts and fees are recorded but are not applied to cash again.
    Each contract has a 30-day term and 15--30 days remaining at initialization.
    """
    local_time = _aware_time(initialized_at, timezone_name)
    utc = ZoneInfo("UTC")
    initialized_iso = local_time.astimezone(utc).isoformat(timespec="seconds")
    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_loan_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute(
            """
            SELECT 1 FROM player_debt_initializations
            WHERE borrower_character_id = ?
            """,
            (borrower_character_id,),
        ).fetchone()
        if marker is not None:
            rows = connection.execute(
                """
                SELECT * FROM player_loan_contracts
                WHERE borrower_character_id = ?
                ORDER BY borrowed_at DESC, loan_id DESC
                """,
                (borrower_character_id,),
            ).fetchall()
            connection.commit()
            return tuple(_contract_from_row(row) for row in rows)

        payload, cash, original_debt, other_assets = _load_character_finances(
            connection, borrower_character_id
        )
        if original_debt == 0:
            connection.execute(
                """
                INSERT INTO player_debt_initializations(
                    borrower_character_id, original_debt, initialized_debt,
                    initialized_at
                ) VALUES (?, 0, 0, ?)
                """,
                (borrower_character_id, initialized_iso),
            )
            connection.commit()
            return ()

        borrower_address = _coordinate(
            payload.get("address"), label=f"借款角色 {borrower_character_id}"
        )
        lenders = _available_lenders_by_distance(connection, borrower_address)
        if not lenders:
            raise LookupError("世界中没有可承接初始负债且具有地址的贷款机构")

        remaining = original_debt
        initialized_debt = 0
        contracts: list[PlayerLoanContract] = []
        rng = random.SystemRandom()
        for _, policy in lenders:
            if remaining <= 0:
                break
            current_principal = _current_lender_principal(
                connection, borrower_character_id, policy.id
            )
            capacity = (
                remaining
                if policy.max_total_debt is None
                else max(0, policy.max_total_debt - current_principal)
            )
            principal = min(remaining, capacity)
            if principal <= 0:
                continue
            quote = quote_loan(policy, current_principal, principal)
            days_remaining = rng.randint(15, 30)
            due_time = local_time + timedelta(days=days_remaining)
            borrowed_time = due_time - timedelta(days=30)
            loan_id = f"LOAN-{uuid.uuid4().hex.upper()}"
            connection.execute(
                """
                INSERT INTO player_loan_contracts(
                    loan_id, borrower_character_id, lender_organization_id,
                    lender_organization_name, borrowed_at, due_at, principal,
                    cash_disbursed, monthly_rate_bps, interest, fee_bps, fee,
                    total_due, status, repaid_at, request_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?)
                """,
                (
                    loan_id,
                    borrower_character_id,
                    policy.id,
                    policy.name,
                    borrowed_time.astimezone(utc).isoformat(timespec="seconds"),
                    due_time.astimezone(utc).isoformat(timespec="seconds"),
                    quote.principal,
                    quote.cash_disbursed,
                    quote.monthly_rate_bps,
                    quote.interest,
                    quote.fee_bps,
                    quote.fee,
                    quote.total_due,
                    f"initial-debt:{borrower_character_id}:{policy.id}",
                ),
            )
            row = connection.execute(
                "SELECT * FROM player_loan_contracts WHERE loan_id = ?", (loan_id,)
            ).fetchone()
            contracts.append(_contract_from_row(row))
            initialized_debt += quote.total_due
            remaining -= principal

        if remaining > 0:
            raise ValueError(f"所有贷款机构容量不足，仍有 ¥{remaining:,} 初始负债无法承接")

        payload.update(
            cash=cash,
            debt=initialized_debt,
            net_assets=cash + other_assets - initialized_debt,
        )
        connection.execute(
            "UPDATE characters SET data_json = ? WHERE character_id = ?",
            (
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                borrower_character_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO player_debt_initializations(
                borrower_character_id, original_debt, initialized_debt,
                initialized_at
            ) VALUES (?, ?, ?, ?)
            """,
            (borrower_character_id, original_debt, initialized_debt, initialized_iso),
        )
        connection.commit()
        return tuple(contracts)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def quote_player_loan(
    database_path: str | Path,
    borrower_character_id: str,
    lender_organization_id: str,
    amount: int,
) -> dict[str, object]:
    """Calculate an authoritative offer for an NPC without changing the world."""
    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    try:
        ensure_loan_schema(connection)
        policy = _load_lender_policy(connection, lender_organization_id)
        _, _, total_debt, _ = _load_character_finances(connection, borrower_character_id)
        quote = quote_loan(policy, total_debt, amount)
        return {
            **quote.as_dict(),
            "lender_organization_name": policy.name,
            "max_total_debt": policy.max_total_debt,
        }
    finally:
        connection.close()



def get_player_lending_capacity(database_path, borrower_character_id, lender_organization_id):
    """All-lender total debt determines remaining principal headroom."""
    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    try:
        ensure_loan_schema(connection)
        policy = _load_lender_policy(connection, lender_organization_id)
        if not policy.offers_loans:
            raise ValueError("该机构不提供贷款")
        _, _, current, _ = _load_character_finances(connection, borrower_character_id)
        return dict(lender_organization_name=policy.name, max_total_debt=policy.max_total_debt,
                    current_debt=current,
                    available_principal=None if policy.max_total_debt is None else max(0, policy.max_total_debt-current),
                    term_months=1, fee_withheld=True)
    finally:
        connection.close()


def negotiate_loan_quote(baseline: LoanQuote, amount: int, monthly_rate_bps: int, fee_bps: int) -> LoanQuote:
    """The model can reduce principal or raise prices, never weaken the floor."""
    for value in (amount, monthly_rate_bps, fee_bps):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63-1:
            raise ValueError("贷款参数必须是有效整数")
    if not 0 < amount <= baseline.principal:
        raise ValueError("成交本金必须大于零且不能超过已评估本金")
    if monthly_rate_bps < baseline.monthly_rate_bps or fee_bps < baseline.fee_bps:
        raise ValueError("成交利率和手续费不得低于标准")
    if fee_bps >= 10000:
        raise ValueError("手续费必须低于本金")
    interest = round(amount * monthly_rate_bps / 10000)
    fee = round(amount * fee_bps / 10000)
    if fee >= amount or amount + interest > 2**63-1:
        raise ValueError("实际到账必须为正且金额不能溢出")
    return replace(baseline, principal=amount, monthly_rate_bps=monthly_rate_bps,
                   fee_bps=fee_bps, interest=interest, fee=fee,
                   cash_disbursed=amount-fee, total_due=amount+interest)


def execute_player_loan(
    database_path: str | Path,
    borrower_character_id: str,
    lender_organization_id: str,
    amount: int,
    *,
    borrowed_at: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
    request_id: str | None = None,
    expected_quote: dict | None = None,
    authorized_npc_id: str | None = None,
) -> PlayerLoanContract:
    """Atomically disburse one player loan after an NPC approves it."""
    request_id = request_id or uuid.uuid4().hex
    local_time = _aware_time(borrowed_at, timezone_name)
    due_time = _add_calendar_month(local_time)
    utc = ZoneInfo("UTC")
    borrowed_iso = local_time.astimezone(utc).isoformat(timespec="seconds")
    due_iso = due_time.astimezone(utc).isoformat(timespec="seconds")

    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_loan_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        existing = _contract_by_request_id(connection, request_id)
        if existing is not None:
            if (
                existing.borrower_character_id != borrower_character_id
                or existing.lender_organization_id != lender_organization_id
                or existing.principal != amount
                or (expected_quote is not None and (
                    existing.monthly_rate_bps != expected_quote.get("monthly_rate_bps")
                    or existing.fee_bps != expected_quote.get("fee_bps")))
            ):
                raise ValueError("request_id 已被另一笔不同的贷款请求使用")
            connection.commit()
            return existing

        policy = _load_lender_policy(connection, lender_organization_id)
        payload, cash, aggregate_debt, other_assets = _load_character_finances(
            connection, borrower_character_id
        )
        baseline_data = expected_quote.get("baseline_quote") if expected_quote else None
        assessed_amount = baseline_data.get("principal") if baseline_data else amount
        quote = quote_loan(policy, aggregate_debt, assessed_amount)
        if expected_quote is not None:
            actual = {**quote.as_dict(), "max_total_debt": policy.max_total_debt}
            comparison = baseline_data if baseline_data else expected_quote
            if any(comparison.get(key) != value for key, value in actual.items()):
                raise ValueError("报价条件已变化，请重新报价并确认")
            if baseline_data:
                quote = negotiate_loan_quote(quote, amount, expected_quote.get("monthly_rate_bps"), expected_quote.get("fee_bps"))
                if any(expected_quote.get(key) != value for key, value in quote.as_dict().items()):
                    raise ValueError("成交参数不合格，请重新报价并确认")
        if authorized_npc_id is not None:
            # Recheck authorization under the same write lock as the money change.
            from .lender_staff_service import get_lender_duty_roster
            roster = get_lender_duty_roster(database_path, lender_organization_id, now=local_time)
            employee = connection.execute(
                "SELECT organization_id FROM characters WHERE character_id=?", (authorized_npc_id,)
            ).fetchone()
            permitted = any(p["character_id"] == authorized_npc_id and p["can_handle_loans"]
                            for p in roster["on_duty"])
            if not employee or employee[0] != lender_organization_id or not permitted:
                raise ValueError("该 NPC 当前无权在此机构放款")
            if payload.get("active_trip") or (payload.get("current_location") or {}).get("organization_id") != lender_organization_id:
                raise ValueError("玩家已离开机构，不能放款")
        new_cash = cash + quote.cash_disbursed
        new_debt = aggregate_debt + quote.total_due
        payload.update(
            cash=new_cash,
            debt=new_debt,
            net_assets=new_cash + other_assets - new_debt,
        )
        connection.execute(
            "UPDATE characters SET data_json = ? WHERE character_id = ?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), borrower_character_id),
        )
        loan_id = f"LOAN-{uuid.uuid4().hex.upper()}"
        connection.execute(
            """
            INSERT INTO player_loan_contracts(
                loan_id, borrower_character_id, lender_organization_id,
                lender_organization_name, borrowed_at, due_at, principal,
                cash_disbursed, monthly_rate_bps, interest, fee_bps, fee,
                total_due, status, repaid_at, request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', NULL, ?)
            """,
            (
                loan_id, borrower_character_id, policy.id, policy.name,
                borrowed_iso, due_iso, quote.principal, quote.cash_disbursed,
                quote.monthly_rate_bps, quote.interest, quote.fee_bps,
                quote.fee, quote.total_due, request_id,
            ),
        )
        row = connection.execute(
            "SELECT * FROM player_loan_contracts WHERE loan_id = ?", (loan_id,)
        ).fetchone()
        connection.commit()
        return _contract_from_row(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def settle_due_player_loans(
    connection: sqlite3.Connection,
    current_time: datetime,
) -> tuple[int, int]:
    """Pay affordable due contracts and mark the rest overdue without charging."""
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("current_time 必须是带时区的 datetime")
    current_iso = current_time.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    rows = connection.execute(
        """
        SELECT loan_id, borrower_character_id, total_due
        FROM player_loan_contracts
        WHERE status = 'active' AND due_at <= ?
        ORDER BY due_at, loan_id
        """,
        (current_iso,),
    ).fetchall()
    paid = 0
    overdue = 0
    for loan_id, character_id, total_due in rows:
        payload, cash, debt, other_assets = _load_character_finances(connection, str(character_id))
        total_due = int(total_due)
        if spend_cash(connection, str(character_id), total_due) == "fail":
            connection.execute(
                "UPDATE player_loan_contracts SET status = 'overdue' WHERE loan_id = ?",
                (str(loan_id),),
            )
            overdue += 1
            continue
        new_cash = cash - total_due
        new_debt = max(0, debt - total_due)
        payload.update(
            cash=new_cash,
            debt=new_debt,
            net_assets=new_cash + other_assets - new_debt,
        )
        connection.execute(
            "UPDATE characters SET data_json = ? WHERE character_id = ?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), str(character_id)),
        )
        connection.execute(
            """
            UPDATE player_loan_contracts
            SET status = 'paid', repaid_at = ?
            WHERE loan_id = ?
            """,
            (current_iso, str(loan_id)),
        )
        paid += 1
    return paid, overdue


def list_player_loan_contracts(
    database_path: str | Path,
    borrower_character_id: str,
) -> tuple[PlayerLoanContract, ...]:
    connection = sqlite3.connect(Path(database_path), timeout=30.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        ensure_loan_schema(connection)
        connection.commit()
        rows = connection.execute(
            """
            SELECT * FROM player_loan_contracts
            WHERE borrower_character_id = ?
            ORDER BY borrowed_at DESC, loan_id DESC
            """,
            (borrower_character_id,),
        ).fetchall()
        return tuple(_contract_from_row(row) for row in rows)
    finally:
        connection.close()


__all__ = (
    "LenderPolicy",
    "ensure_loan_schema",
    "execute_player_loan",
    "lending_blackness",
    "list_player_loan_contracts",
    "materialize_player_debt",
    "quote_loan",
    "quote_player_loan",
    "settle_due_player_loans",
)
