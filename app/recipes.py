"""Data-driven content recipe helpers."""

from . import db


def active() -> list[dict]:
    return db.all_("SELECT * FROM content_recipes WHERE active=1 ORDER BY sort, id")


def by_id(recipe_id: int):
    return db.one("SELECT * FROM content_recipes WHERE id=? AND active=1", (recipe_id,))
