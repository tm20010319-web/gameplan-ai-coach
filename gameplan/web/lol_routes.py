"""Shared read-only LoL routes for both existing application entry points."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from gameplan.paths import ROOT
from gameplan.knowledge import lol_knowledge

router = APIRouter()


@router.get("/knowledge/lol")
def page():
    return FileResponse(ROOT / "static/lol-knowledge.html")


@router.get("/api/knowledge/lol/heroes")
def heroes(q: str = Query("", max_length=200), role: str = Query("", max_length=20)):
    return lol_knowledge.search(q, role)


@router.get("/api/knowledge/lol/heroes/{hero_id}")
def hero(hero_id: str):
    record = lol_knowledge.get_hero(hero_id)
    if record is None:
        raise HTTPException(404, "英雄联盟知识库中没有这个英雄")
    return record
