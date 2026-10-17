"""Messages (the bell), message choices (S28 "How we tell you things") and feature flags.

- `GET /v1/notifications`: the person's own in-app messages, newest first (`unread=true` for
  unread only), the unread count and whether their email address bounced (F33 banner).
- `POST /v1/notifications/{id}/read`: marks one read; the first time is kept. Anyone else's is 404.
- `GET/PUT /v1/me/notification-preferences`: per current organisation, in the app / by email /
  one email a day, and muted kinds. Nothing saved means in the app and by email at once.
- `GET /v1/feature-flags`: the flags that apply to this person (`app.core.flags`).
All through row-level security as the signed-in person.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.core import flags
from app.core.errors import ApiError
from app.core.http import DbConn, router

notifications_router = router(prefix="/v1", tags=["notifications"])

Kind = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]


class Notification(BaseModel):
    id: uuid.UUID
    kind: str
    organisation_id: uuid.UUID
    case_id: uuid.UUID | None
    case_label: str | None
    created_at: datetime
    read_at: datetime | None


class Notifications(BaseModel):
    items: list[Notification]
    unread_count: int
    email_bounced: bool


class NotificationChoice(BaseModel):
    organisation_id: uuid.UUID
    in_app: bool
    email: bool
    digest: bool
    muted_kinds: list[Kind] = Field(default_factory=list, max_length=100)


class NotificationPreference(NotificationChoice):
    organisation_name: str
    organisation_kind: str


class FeatureFlags(BaseModel):
    flags: dict[str, bool]


_SELECT = """
    select n.id, n.kind, n.organisation_id, n.case_id, c.label, n.created_at, n.read_at
    from app.notifications n left join app.cases c on c.id = n.case_id
    where n.person_id = app.current_person_id() and n.in_app and n.held_at is null
"""


def _item(row: tuple[object, ...]) -> Notification:
    keys = ("id", "kind", "organisation_id", "case_id", "case_label", "created_at", "read_at")
    return Notification.model_validate(dict(zip(keys, row, strict=True)))


@notifications_router.get("/notifications", response_model=Notifications)
async def list_notifications(conn: DbConn, unread: bool = False) -> Notifications:
    cur = await conn.execute(
        _SELECT + (" and n.read_at is null" if unread else "") + " order by n.created_at desc, n.id limit 200"
    )
    items = [_item(r) for r in await cur.fetchall()]
    cur = await conn.execute(
        "select count(*) from app.notifications"
        " where person_id = app.current_person_id() and in_app and held_at is null and read_at is null"
    )
    unread_count = (await cur.fetchone() or (0,))[0]
    cur = await conn.execute("select email_bounced_at is not null from app.people where id = app.current_person_id()")
    bounced = await cur.fetchone()
    return Notifications(items=items, unread_count=unread_count, email_bounced=bool(bounced and bounced[0]))


@notifications_router.post("/notifications/{notification_id}/read", response_model=Notification)
async def mark_read(notification_id: uuid.UUID, conn: DbConn) -> Notification:
    await conn.execute(
        "update app.notifications set read_at = now() where id = %s and read_at is null", (notification_id,)
    )
    cur = await conn.execute(_SELECT + " and n.id = %s", (notification_id,))
    row = await cur.fetchone()
    if row is None:
        raise ApiError("not_found", "Not found.", 404)
    return _item(row)


async def _preferences(conn: DbConn) -> list[NotificationPreference]:
    cur = await conn.execute(
        """
        select o.id, o.name, o.kind,
               coalesce(a.enabled, true), coalesce(e.enabled, true), coalesce(e.digest, false),
               coalesce(e.muted_kinds, a.muted_kinds, '{}')
        from app.memberships m
        join app.organisations o on o.id = m.organisation_id
        left join app.notification_preferences a
          on a.person_id = m.person_id and a.organisation_id = o.id and a.channel = 'in_app'
        left join app.notification_preferences e
          on e.person_id = m.person_id and e.organisation_id = o.id and e.channel = 'email'
        where m.person_id = app.current_person_id() and m.ended_at is null
        order by o.kind = 'team', o.name, o.id
        """
    )
    return [
        NotificationPreference(
            organisation_id=r[0],
            organisation_name=r[1],
            organisation_kind=r[2],
            in_app=r[3],
            email=r[4],
            digest=r[5],
            muted_kinds=list(r[6]),
        )
        for r in await cur.fetchall()
    ]


@notifications_router.get("/me/notification-preferences", response_model=list[NotificationPreference])
async def get_preferences(conn: DbConn) -> list[NotificationPreference]:
    return await _preferences(conn)


@notifications_router.put("/me/notification-preferences", response_model=list[NotificationPreference])
async def put_preferences(choices: list[NotificationChoice], conn: DbConn) -> list[NotificationPreference]:
    """Saves the choices for each organisation named; others are left as they were."""
    cur = await conn.execute("select app.my_org_ids()")
    mine = set((await cur.fetchone() or ([],))[0])
    for choice in choices:
        if choice.organisation_id not in mine:
            raise ApiError("not_found", "Not found.", 404)
        muted = sorted(set(choice.muted_kinds))
        for channel, enabled, digest in (("in_app", choice.in_app, False), ("email", choice.email, choice.digest)):
            await conn.execute(
                """
                insert into app.notification_preferences
                  (person_id, organisation_id, channel, enabled, digest, muted_kinds)
                values (app.current_person_id(), %s, %s, %s, %s, %s)
                on conflict (person_id, organisation_id, channel)
                do update set enabled = excluded.enabled, digest = excluded.digest,
                              muted_kinds = excluded.muted_kinds, updated_at = now()
                """,
                (choice.organisation_id, channel, enabled, digest, muted),
            )
    return await _preferences(conn)


@notifications_router.get("/feature-flags", response_model=FeatureFlags)
async def feature_flags(conn: DbConn) -> FeatureFlags:
    return FeatureFlags(flags=await flags.evaluate(conn))
