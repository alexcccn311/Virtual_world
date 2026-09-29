"""Read default contacts from generated relationships without scanning the world."""
import json
import sqlite3
from contextlib import closing

from .character_catalog import _readonly_connection, _display_list
from pathlib import Path


def list_character_contacts(database_path, character_id):
    """Return family and direct superior once each, excluding self/missing IDs."""
    with closing(_readonly_connection(Path(database_path))) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT data_json FROM characters WHERE character_id=?", (character_id,)).fetchone()
        if row is None:
            raise LookupError("角色不存在")
        relation = json.loads(row["data_json"]).get("relation") or {}
        contacts = {}
        for key, label in (
            ("father_id", "父亲"), ("mother_id", "母亲"), ("spouse_id", "配偶"),
            ("children_ids", "子女"), ("older_sibling_ids", "兄姐"),
            ("younger_sibling_ids", "弟妹"), ("older_sibling_id", "兄姐"),
            ("younger_sibling_id", "弟妹"), ("sibling_ids", "兄弟姐妹"),
            ("superior_id", "直属上级"),
        ):
            values = relation.get(key, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, (list, tuple)):
                continue
            for contact_id in values:
                if not isinstance(contact_id, str) or not contact_id or contact_id == character_id:
                    continue
                labels = contacts.setdefault(contact_id, [])
                if label == "兄弟姐妹" and any(item in labels for item in ("兄姐", "弟妹")):
                    continue
                if label not in labels:
                    labels.append(label)
        result = []
        for contact_id, labels in contacts.items():
            row = connection.execute("SELECT * FROM characters WHERE character_id=?", (contact_id,)).fetchone()
            if row is None:
                continue
            data = json.loads(row["data_json"])
            name = str(row["name"] or "未命名角色")
            profile = {
                "姓名": name, "昵称": _display_list(data.get("nickname"), "无"),
                "关系": "、".join(labels),
                "性别": {"female": "女", "male": "男"}.get(data.get("sex"), "未知"),
                "年龄": f'{data["age"]} 岁' if data.get("age") is not None else "未知",
                "职业": row["occupation"] or "暂无",
                "所属组织": row["organization_name"] or "无",
                "职位": row["title"] or "无",
                "所在城区": row["district_name"] or "未知",
                "所在街道": row["street_name"] or "未知",
                "学历": _display_list(data.get("education")),
                "性格": _display_list(data.get("personality_tags")),
                "爱好": _display_list(data.get("hobbies")),
            }
            result.append(dict(id=contact_id, name=name, relationship="、".join(labels),
                               profile=profile, photo_path=data.get("photo_path") or ""))
    return result

