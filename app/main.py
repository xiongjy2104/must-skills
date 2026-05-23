from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, status

from .models import Skill, SkillCreate, SkillUpdate
from .storage import store

app = FastAPI(
    title="must-skills API",
    description="A minimal API for tracking skills you must learn.",
    version="0.1.0",
)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/skills", response_model=List[Skill], tags=["skills"])
def list_skills(category: Optional[str] = Query(default=None)) -> List[Skill]:
    return store.list(category=category)


@app.post(
    "/skills",
    response_model=Skill,
    status_code=status.HTTP_201_CREATED,
    tags=["skills"],
)
def create_skill(payload: SkillCreate) -> Skill:
    return store.create(payload)


@app.get("/skills/{skill_id}", response_model=Skill, tags=["skills"])
def get_skill(skill_id: int) -> Skill:
    skill = store.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@app.patch("/skills/{skill_id}", response_model=Skill, tags=["skills"])
def update_skill(skill_id: int, payload: SkillUpdate) -> Skill:
    updated = store.update(skill_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return updated


@app.delete(
    "/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["skills"],
)
def delete_skill(skill_id: int) -> None:
    if not store.delete(skill_id):
        raise HTTPException(status_code=404, detail="Skill not found")
