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
    REFERENCE_CITY_DATABASE,
    WORLD_DATABASE_DIRECTORY,
    character_world_by_id,
    discover_character_worlds,
    draw_reference_character,
)

__all__ = (
    "build_body_description",
    "build_body_metrics_text",
    "build_character_card_summary",
    "build_character_descriptions",
    "build_character_portrait_prompt",
    "build_face_description",
    "CharacterWorldDatabase",
    "DAILY_CHARACTER_OCCUPATIONS",
    "REFERENCE_CITY_DATABASE",
    "WORLD_DATABASE_DIRECTORY",
    "character_world_by_id",
    "discover_character_worlds",
    "draw_reference_character",
)
