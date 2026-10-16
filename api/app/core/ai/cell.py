"""The cell allow-list (rule 5, F24): which model IDs and AI endpoints this cell may use."""

from __future__ import annotations

from app.core.config import AiSettings, endpoint_allowed, in_region_model_id


class ModelNotAllowed(Exception):
    """The model ID is not one of the cell's in-region models."""


class EndpointNotAllowed(Exception):
    """The endpoint is not on the cell's allow-list (or not secure)."""


def check_model(settings: AiSettings, model_id: str) -> str:
    if model_id not in settings.models.values() or not in_region_model_id(model_id):
        raise ModelNotAllowed("model is not one of this cell's in-region models")
    return model_id


def model_for(settings: AiSettings, role: str) -> str:
    """The in-region model ID for a task's model role (`sonnet`, `opus`)."""
    model_id = settings.models.get(role)
    if model_id is None:
        raise ModelNotAllowed("no in-region model for this role")
    return check_model(settings, model_id)


def check_endpoint(settings: AiSettings, url: str) -> str:
    if not endpoint_allowed(url, settings.allowed_hosts):
        raise EndpointNotAllowed("endpoint is not on this cell's allow-list")
    return url
