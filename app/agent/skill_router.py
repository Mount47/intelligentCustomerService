"""Skill 路由：intent -> Skill。无业务 Skill 认领则落兜底 GeneralSkill。

M5 在此注册 RefundHandlingSkill / LogisticsExceptionSkill。
"""
from __future__ import annotations

from app.agent.context import Skill
from app.core.logging import get_logger
from app.skills.general import GeneralSkill

logger = get_logger(__name__)


class SkillRouter:
    def __init__(self, skills: list[Skill] | None = None, default: Skill | None = None) -> None:
        self._default = default or GeneralSkill()
        self._skills = skills or []

    def register(self, skill: Skill) -> None:
        self._skills.append(skill)

    def route(self, intent: str) -> Skill:
        for skill in self._skills:
            if intent in skill.triggers:
                logger.info("route intent=%s -> skill=%s", intent, skill.name)
                return skill
        logger.info("route intent=%s -> skill=%s(default)", intent, self._default.name)
        return self._default
