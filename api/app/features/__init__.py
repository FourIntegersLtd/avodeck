"""One package per slice (`app.features.<name>`), found and loaded by `app.core.features`.

A feature's `router` (its `router` module's `router`, or a `router` attribute on the package) is
mounted by `create_app()`; its `jobs` module is imported by the worker so its job kinds and
schedules register on `app.core.jobs.registry`.
"""
