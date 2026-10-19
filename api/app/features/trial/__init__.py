"""Slice I, trial: expert meetings and joint statements (S22), trial page numbers (S23), the trial pack (S24)
and the offline witness-box copy (S25). The e-sign webhook is public (`webhook_router`, mounted by main)."""

from app.features.trial.router import router
from app.features.trial.routes_webhook import webhook_router

__all__ = ["router", "webhook_router"]
