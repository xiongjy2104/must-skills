from datetime import datetime, timezone
from threading import Lock
from typing import Dict, List, Optional

from .models import Skill, SkillCreate, SkillUpdate


class SkillStore:
    def __init__(self) -> None:
        self._items: Dict[int, Skill] = {}
        self._next_id: int = 1
        self._lock = Lock()

    def list(self, category: Optional[str] = None) -> List[Skill]:
        with self._lock:
            items = list(self._items.values())
        if category:
            items = [s for s in items if s.category == category]
        return sorted(items, key=lambda s: s.id)

    def get(self, skill_id: int) -> Optional[Skill]:
        with self._lock:
            return self._items.get(skill_id)

    def create(self, data: SkillCreate) -> Skill:
        now = datetime.now(timezone.utc)
        with self._lock:
            skill = Skill(
                id=self._next_id,
                created_at=now,
                updated_at=now,
                **data.model_dump(),
            )
            self._items[self._next_id] = skill
            self._next_id += 1
            return skill

    def update(self, skill_id: int, data: SkillUpdate) -> Optional[Skill]:
        with self._lock:
            existing = self._items.get(skill_id)
            if existing is None:
                return None
            changes = data.model_dump(exclude_unset=True)
            updated = existing.model_copy(update={
                **changes,
                "updated_at": datetime.now(timezone.utc),
            })
            self._items[skill_id] = updated
            return updated

    def delete(self, skill_id: int) -> bool:
        with self._lock:
            return self._items.pop(skill_id, None) is not None

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._next_id = 1


store = SkillStore()
