"""Runtime services used by world generation."""

from .character_description import (
    build_body_description,
    build_body_metrics_text,
    build_character_card_summary,
    build_character_descriptions,
    build_character_portrait_prompt,
    build_face_description,
)
from .character_catalog import (
    CharacterWorldDatabase,
    DAILY_CHARACTER_OCCUPATIONS,
    WORLD_DATABASE_DIRECTORY,
    character_world_by_id,
    discover_character_worlds,
    draw_world_character,
)
from .bus_service import (
    BusOperatingSchedule,
    BusStop,
    BusTripEstimate,
    LoadedBusSystem,
    RoadNetwork,
    estimate_bus_trip,
    load_bus_system,
    shortest_road_distance_m,
)

__all__ = (
    "build_body_description",
    "build_body_metrics_text",
    "build_character_card_summary",
    "build_character_descriptions",
    "build_character_portrait_prompt",
    "build_face_description",
    "BusOperatingSchedule",
    "BusStop",
    "BusTripEstimate",
    "CharacterWorldDatabase",
    "DAILY_CHARACTER_OCCUPATIONS",
    "WORLD_DATABASE_DIRECTORY",
    "character_world_by_id",
    "discover_character_worlds",
    "draw_world_character",
    "estimate_bus_trip",
    "LoadedBusSystem",
    "load_bus_system",
    "RoadNetwork",
    "shortest_road_distance_m",
)
