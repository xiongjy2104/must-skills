from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SkillLevel(str, Enum):
    novice = "novice"
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"
    expert = "expert"


class SkillBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    category: str = Field(..., min_length=1, max_length=50)
    level: SkillLevel = SkillLevel.novice
    notes: Optional[str] = Field(None, max_length=1000)


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    level: Optional[SkillLevel] = None
    notes: Optional[str] = Field(None, max_length=1000)


class Skill(SkillBase):
    id: int
    created_at: datetime
    updated_at: datetime
