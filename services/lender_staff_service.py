"""Deterministic repeating lender shifts and player-facing receptionist selection.

No stored random assignments and no full-world scans. NPC availability is derived
from the current organization's actual role holders, in Beijing time.
"""
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .movement_service import get_movement_state

# Template keys and role keys, never translated occupation/title matching.
LENDING_ROLES = {
    "neighborhood_lender": ("owner", "loan_clerk"),
    "underground_lender": ("loan_officer", "lending_manager"),
    "loan_shark_chain_1": ("lending_review_staff",),
    "loan_shark_chain_store_1": ("lending_staff",),
    "casino_1": ("floor_credit_agent",),
}
RECEPTION_ROLES = {"loan_shark_chain_store_1": ("customer_relations_staff",)}
THREE_SHIFTS = ((0, 480), (480, 960), (960, 1440))
TWO_SHIFTS = ((0, 720), (720, 1440))
SINGLE_SHIFT = ((540, 1080),)


def _beijing(now):
    value = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return value.astimezone(ZoneInfo("Asia/Shanghai"))


def _rank(*parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).digest()


def _clock(minute):
    return f"{minute // 60:02d}:{minute % 60:02d}"


def get_lender_duty_roster(database_path, organization_id, *, now=None):
    """Return the full recurring roster and who is on duty at this instant.

    Half-open intervals [start, end) guarantee an exact handover at boundaries.
    Staff changes recalculate the roster; query calls never modify world data.
    """
    current = _beijing(now)
    minute = current.hour * 60 + current.minute
    with closing(sqlite3.connect(Path(database_path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute(
            "SELECT name,template_name,data_json FROM organizations WHERE organization_id=?",
            (organization_id,),
        ).fetchone()
        if row is None:
            raise LookupError("贷款机构不存在")
        name, template, raw = row
        if template not in LENDING_ROLES or json.loads(raw).get("offers_loans") is not True:
            raise ValueError("该组织未开放贷款柜台")
        roles = LENDING_ROLES[template] + RECEPTION_ROLES.get(template, ())
        rows = conn.execute(
            f"""SELECT character_id,name,occupation,title,
                       json_extract(data_json,'$.organization_role')
                FROM characters WHERE organization_id=?
                AND json_extract(data_json,'$.organization_role') IN ({','.join('?' for _ in roles)})
                ORDER BY character_id""",
            (organization_id, *roles),
        ).fetchall()
    handlers = [r for r in rows if r[4] in LENDING_ROLES[template]]
    assistants = [r for r in rows if r[4] in RECEPTION_ROLES.get(template, ())]
    # ponytail: fixed daily repeat, no leave/weekend simulation yet; add a
    # dated override roster when NPC absences become actual gameplay state.
    shifts = THREE_SHIFTS if len(handlers) >= 3 else TWO_SHIFTS if len(handlers) == 2 else SINGLE_SHIFT
    staff = []
    for group, can_handle in ((handlers, True), (assistants, False)):
        for index, (cid, employee_name, occupation, title, role) in enumerate(
            sorted(group, key=lambda item: _rank(organization_id, item[0]))
        ):
            start, end = shifts[index % len(shifts)]
            staff.append(dict(
                character_id=cid, name=employee_name, occupation=occupation, title=title,
                organization_role=role, can_handle_loans=can_handle,
                start_minute=start, end_minute=end,
                starts_at=_clock(start), ends_at=_clock(end),
                on_duty=start <= minute < end,
            ))
    staff.sort(key=lambda item: (item["start_minute"], not item["can_handle_loans"], item["character_id"]))
    on_duty = [person for person in staff if person["on_duty"]]
    is_open = any(person["can_handle_loans"] for person in on_duty)
    return dict(
        organization_id=organization_id, organization_name=name, template=template,
        at=current.isoformat(), timezone="Asia/Shanghai",
        opening_hours="24小时" if len(handlers) >= 2 else "09:00–18:00" if handlers else "暂无办理人员",
        is_open=is_open, staff=staff, on_duty=on_duty,
    )


def select_lender_receptionist(database_path, organization_id, player_id, *, now=None, previous_npc_id=None):
    """Preview a stable assignment; this does not authorize a remote meeting.

    A caller with an ongoing conversation should pass previous_npc_id and act
    on handover_required before binding another NPC's agent or memories.
    """
    if not isinstance(player_id, str) or not player_id.strip():
        raise ValueError("player_id 不能为空")
    roster = get_lender_duty_roster(database_path, organization_id, now=now)
    eligible = [
        person for person in roster["on_duty"]
        if person["can_handle_loans"] and person["character_id"] != player_id
    ]
    receptionist = next((p for p in eligible if p["character_id"] == previous_npc_id), None)
    if receptionist is None and eligible:
        receptionist = min(eligible, key=lambda p: _rank(organization_id, player_id, p["character_id"]))
    return dict(
        **roster, receptionist=receptionist,
        handover_required=bool(previous_npc_id and (
            receptionist is None or receptionist["character_id"] != previous_npc_id
        )),
    )


def get_player_lender_reception(database_path, player_id, *, now=None, previous_npc_id=None):
    """Resolve actual arrival first, then the current lender and on-duty NPC.

    Movement settlement may persist the player's elapsed trip, but this function
    does not issue loans, create agents or save conversations.
    """
    current = _beijing(now)
    state = get_movement_state(database_path, player_id, now=current)
    if state["trip"]:
        raise ValueError("你还在路上，抵达后再办理业务吧")
    organization_id = state["location"].get("organization_id")
    if not organization_id:
        raise ValueError("请先到贷款机构，再找工作人员办理业务")
    return select_lender_receptionist(
        database_path, organization_id, player_id, now=current,
        previous_npc_id=previous_npc_id,
    )
