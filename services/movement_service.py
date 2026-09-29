"""Persistent wall-clock player travel with atomic, idempotent confirmation."""
import json
import math
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .. import config
from ..character_config import SEX_SERVICE_PROFESSIONS
from .sex_service import _period_minute_bounds
from .cash_service import can_afford, spend_cash

from ..generators.urban_layout_generator import HEX_SIDE_LENGTH_M
from .city_map_service import _cell, _cell_center, _position, ORGANIZATION_MARKERS

WALKING_SPEED_KMH = 4.5
BUS_FARE = 5


def initialize_player_location(database_path, character_id, *, now=None):
    """Set a new player's location once, in the world's Shanghai timezone."""
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    current = current.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if current.tzinfo is None else current.astimezone(ZoneInfo("Asia/Shanghai"))
    minute = current.hour * 60 + current.minute
    with closing(sqlite3.connect(f"{Path(database_path).resolve().as_uri()}?mode=rw", uri=True)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        city_id, payload, _ = _player(connection, character_id)
        if payload.get("current_location"):
            return payload["current_location"]
        row = connection.execute("SELECT organization_id, occupation FROM characters WHERE character_id=?", (character_id,)).fetchone()
        organization = connection.execute("SELECT template_name FROM organizations WHERE city_id=? AND organization_id=?", (city_id, row[0])).fetchone()
        periods = config.SEX_SERVICE_VENUE_OPEN_PERIODS.get(organization[0], ()) if organization and row[1] in SEX_SERVICE_PROFESSIONS else ()
        working = any(start <= minute < end if start < end else minute >= start or minute < end
                      for start, end in map(_period_minute_bounds, periods))
        if working:
            location = _destination(connection, city_id, row[0], "organization")
        else:
            address = _cell(payload.get("address"), label="住址")
            district, street = "", ""
            for district_name, street_name, raw in connection.execute("SELECT district_name, name, data_json FROM streets WHERE city_id=?", (city_id,)):
                if list(address) in json.loads(raw).get("cells", []):
                    district, street = district_name, street_name
                    break
            location = dict(name="家", position=list(_cell_center(address)), district=district,
                            street=street, stop_id=None, organization_id=None)
        payload["current_location"] = location
        payload["location_initialized_at"] = current.isoformat()
        connection.execute("UPDATE characters SET data_json=? WHERE character_id=?", (json.dumps(payload, ensure_ascii=False), character_id))
    return location


def _boarding_stop(connection, city_id, payload, origin):
    stop_id = (payload.get("current_location") or {}).get("stop_id")
    if not stop_id or _stop(connection, city_id, stop_id)["position"] != list(origin):
        raise ValueError("请先步行到公交站")
    return stop_id


def _check_fare(payload):
    cash = payload.get("cash", 0)
    if not can_afford(cash, BUS_FARE):
        raise ValueError("现金余额不足，乘坐公交需要 5 元")


def _player(connection, character_id):
    row = connection.execute("SELECT city_id, data_json FROM characters WHERE character_id=?", (character_id,)).fetchone()
    if row is None:
        raise LookupError("角色不存在")
    payload = json.loads(row[1])
    location = payload.get("current_location")
    position = _position(location["position"], label="当前位置") if location else _cell_center(_cell(payload.get("address"), label="住址"))
    if not all(map(math.isfinite, position)):
        raise ValueError("当前位置无效")
    return row[0], payload, position


def _stop(connection, city_id, stop_id):
    row = connection.execute("SELECT name, district_name, street_name, position_x, position_y FROM bus_stops WHERE city_id=? AND stop_id=?", (city_id, stop_id)).fetchone()
    if row is None:
        raise LookupError("公交站不存在或不属于当前城市")
    position = _position(row[3:5], label="公交站")
    if not all(map(math.isfinite, position)):
        raise ValueError("公交站坐标无效")
    return dict(stop_id=stop_id, name=row[0], district=row[1], street=row[2], position=list(position))


def _destination(connection, city_id, target_id, target_kind):
    if target_kind == "bus_stop":
        return _stop(connection, city_id, target_id)
    if target_kind != "organization":
        raise ValueError("无效的目的地类型")
    row = connection.execute("SELECT name, district_name, street_name, template_name, data_json FROM organizations WHERE city_id=? AND organization_id=?", (city_id, target_id)).fetchone()
    if row is None or row[3] not in ORGANIZATION_MARKERS:
        raise LookupError("组织不存在或不是可前往的地图组织")
    data = json.loads(row[4])
    position = _position(data["position"], label="组织位置") if data.get("position") is not None else _cell_center(_cell(data.get("address"), label="组织地址"))
    if not all(map(math.isfinite, position)):
        raise ValueError("组织坐标无效")
    return dict(organization_id=target_id, stop_id=None, name=row[0], district=row[1], street=row[2], position=list(position))


def estimate_move_to_bus_stop(database_path, character_id, stop_id, bus_system, *, mode="walk", target_kind="bus_stop", now=None):
    """The returned quote must stay server-side; never accept it from a client."""
    get_movement_state(database_path, character_id, now=now)
    if mode not in ("walk", "bus"):
        raise ValueError("无效的交通方式")
    with closing(sqlite3.connect(f"{Path(database_path).resolve().as_uri()}?mode=ro", uri=True)) as connection:
        city_id, payload, origin = _player(connection, character_id)
        if payload.get("active_trip"):
            raise ValueError("移动尚未结束，请先停止当前行程")
        target = _destination(connection, city_id, stop_id, target_kind)
        if mode == "bus":
            if target_kind != "bus_stop":
                raise ValueError("公交仅支持站点之间移动")
            if _boarding_stop(connection, city_id, payload, origin) == stop_id:
                raise ValueError("已经在目标公交站，无需乘车")
            _check_fare(payload)
    if city_id != bus_system.city_id:
        raise ValueError("路网不属于当前城市")
    distance, route = bus_system.road_network.shortest_route(origin, target["position"], include_access=True)
    speed = bus_system.schedule.speed_kmh if mode == "bus" else WALKING_SPEED_KMH
    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("交通速度配置无效")
    return dict(id=uuid.uuid4().hex, character_id=character_id, city_id=city_id,
                mode=mode, fare=BUS_FARE if mode == "bus" else 0, speed_kmh=speed,
                target_kind=target_kind, route=route,
                origin=list(origin), revision=payload.get("last_walk"), target=target,
                distance_m=distance, minutes=math.ceil(distance / (speed * 1000 / 60)))


def confirm_move_to_bus_stop(database_path, character_id, quote, *, now=None):
    """Start a persistent trip; confirmation never advances the world clock."""
    with closing(sqlite3.connect(f"{Path(database_path).resolve().as_uri()}?mode=rw", uri=True)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        city_id, payload, origin = _player(connection, character_id)
        if quote["character_id"] != character_id or quote["city_id"] != city_id:
            raise ValueError("移动请求不属于当前角色")
        if (payload.get("last_walk") or {}).get("id") == quote["id"]:
            return payload["current_location"]
        if payload.get("active_trip"):
            raise ValueError("移动尚未结束")
        if list(origin) != quote["origin"] or payload.get("last_walk") != quote["revision"]:
            raise ValueError("当前位置已变化，请重新计算")
        target_kind = quote.get("target_kind", "bus_stop")
        target_id = quote["target"].get("organization_id") if target_kind == "organization" else quote["target"]["stop_id"]
        target = _destination(connection, city_id, target_id, target_kind)
        if target != quote["target"]:
            raise ValueError("公交站已变化，请重新计算")
        mode = quote.get("mode", "walk")
        if mode not in ("walk", "bus"):
            raise ValueError("无效的交通方式")
        if mode == "bus":
            if target_kind != "bus_stop":
                raise ValueError("公交仅支持站点之间移动")
            if _boarding_stop(connection, city_id, payload, origin) == target["stop_id"]:
                raise ValueError("已经在目标公交站，无需乘车")
            if spend_cash(connection, character_id, BUS_FARE) == "fail":
                raise ValueError("现金余额不足，乘坐公交需要 5 元")
            _, payload, _ = _player(connection, character_id)
        trip = {key: quote[key] for key in ("id", "origin", "target", "distance_m", "minutes", "route", "speed_kmh", "mode")}
        trip["started_at"] = _now(now).isoformat()
        trip["cumulative"] = [0.0]
        for a, b in zip(trip["route"], trip["route"][1:]):
            trip["cumulative"].append(trip["cumulative"][-1] + math.dist(a, b) * HEX_SIDE_LENGTH_M)
        trip["distance_m"] = trip["cumulative"][-1]
        trip["stops"] = []
        if mode == "bus":
            for (stop_id,) in connection.execute("SELECT stop_id FROM bus_stops WHERE city_id=?", (city_id,)).fetchall():
                stop = _stop(connection, city_id, stop_id)
                distance = _route_stop_distance(trip, stop["position"])
                if distance is not None and distance > 1e-6:
                    trip["stops"].append(dict(distance_m=distance, target=stop))
            trip["stops"].append(dict(distance_m=trip["distance_m"], target=target))
            trip["stops"].sort(key=lambda item: item["distance_m"])
        payload["active_trip"] = trip
        payload["last_walk"] = dict(id=trip["id"], recorded_at=trip["started_at"])
        _advance(payload, _now(now))
        connection.execute("UPDATE characters SET data_json=? WHERE character_id=?", (json.dumps(payload, ensure_ascii=False), character_id))
    return payload["current_location"]


# Compatibility for existing walking callers.
estimate_walk_to_bus_stop = estimate_move_to_bus_stop
confirm_walk_to_bus_stop = confirm_move_to_bus_stop


def _now(value=None):
    value = value or datetime.now(timezone.utc)
    return value.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if value.tzinfo is None else value


def _route_stop_distance(trip, position):
    for index, (a, b) in enumerate(zip(trip["route"], trip["route"][1:])):
        dx, dy = b[0]-a[0], b[1]-a[1]
        fraction = ((position[0]-a[0])*dx + (position[1]-a[1])*dy) / (dx*dx+dy*dy)
        if -1e-9 <= fraction <= 1+1e-9 and math.dist(position, (a[0]+fraction*dx, a[1]+fraction*dy)) < 1e-8:
            return trip["cumulative"][index] + max(0, min(1, fraction)) * math.hypot(dx, dy) * HEX_SIDE_LENGTH_M
    return None


def _advance(payload, now, *, stop=False):
    trip = payload.get("active_trip")
    if not trip:
        return
    elapsed = max(0, (now - datetime.fromisoformat(trip["started_at"])).total_seconds())
    progress = min(trip["distance_m"], elapsed * trip["speed_kmh"] / 3.6)
    position = trip["route"][-1]
    for index, end in enumerate(trip["cumulative"][1:], 1):
        if progress <= end:
            start = trip["cumulative"][index-1]
            fraction = (progress-start)/(end-start)
            a, b = trip["route"][index-1:index+1]
            position = [a[0]+fraction*(b[0]-a[0]), a[1]+fraction*(b[1]-a[1])]
            break
    arrived = progress >= trip["distance_m"]
    payload["current_location"] = trip["target"] if arrived else dict(
        position=position, name="途中", district="", street="", stop_id=None, organization_id=None)
    if stop and not arrived and trip["mode"] == "bus":
        next_stop = next(item for item in trip["stops"] if item["distance_m"] > progress)
        trip["target"] = next_stop["target"]
        trip["distance_m"] = next_stop["distance_m"]
        keep = [i for i, distance in enumerate(trip["cumulative"]) if distance < trip["distance_m"]]
        trip["route"] = [trip["route"][i] for i in keep] + [trip["target"]["position"]]
        trip["cumulative"] = [trip["cumulative"][i] for i in keep] + [trip["distance_m"]]
        trip["stopping"] = True
    if arrived or (stop and trip["mode"] == "walk"):
        key = "bus_minutes_total" if trip["mode"] == "bus" else "walking_minutes_total"
        payload[key] = payload.get(key, 0) + progress / (trip["speed_kmh"]*1000/60)
        if not arrived:
            payload["current_location"]["name"] = "步行停留处"
        payload["last_walk"]["finished_at"] = now.isoformat()
        payload.pop("active_trip")
    else:
        trip["remaining_seconds"] = max(0, (trip["distance_m"]-progress)/(trip["speed_kmh"]/3.6))


def get_movement_state(database_path, character_id, *, now=None, stop=False, trip_id=None):
    """Lazy wall-clock settlement, including offline time; no background worker."""
    with closing(sqlite3.connect(f"{Path(database_path).resolve().as_uri()}?mode=rw", uri=True)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        _, payload, position = _player(connection, character_id)
        if stop and (not trip_id or (payload.get("active_trip") or {}).get("id") != trip_id):
            raise ValueError("行程已变化，请刷新后重试")
        had_trip = bool(payload.get("active_trip"))
        _advance(payload, _now(now), stop=stop)
        if had_trip:
            connection.execute("UPDATE characters SET data_json=? WHERE character_id=?",
                               (json.dumps(payload, ensure_ascii=False), character_id))
        return dict(location=payload.get("current_location") or dict(position=list(position), name="家", district="", street=""),
                    trip=payload.get("active_trip"))
