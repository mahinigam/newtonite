"""Object factories for tests.

Kept separate from `conftest.py` so both fixtures and test modules can import them,
and so arranging a scenario reads as a sentence: create a team, add a member, create
an item assigned to them.

Deliberate shortcuts, with reasons:

* Identifiers are generated in Python rather than by the database default, so a test
  can use an id immediately without a refresh round trip.
* One password hash is computed once and shared. Argon2id is intentionally slow, and
  the suite creates dozens of users; distinct salts matter for real accounts, not for
  fixtures. The password *verification* path is still exercised for real.
"""

from __future__ import annotations

import uuid

from app.models import Comment, Membership, Team, User, WorkItem
from app.models.enums import Priority, Role, Status, WorkItemType
from app.security.passwords import hash_password
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

TEST_PASSWORD = "test-password-123"
_PASSWORD_HASH = hash_password(TEST_PASSWORD)

DEFAULT_TITLE = "Investigate the failing nightly job"


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email.strip().lower(),
        display_name=display_name or email.split("@")[0].replace(".", " ").title(),
        password_hash=_PASSWORD_HASH,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    return user


async def create_team(
    session: AsyncSession, *, key: str, name: str | None = None, description: str = ""
) -> Team:
    team = Team(id=uuid.uuid4(), key=key, name=name or key.title(), description=description)
    session.add(team)
    await session.flush()
    return team


async def add_member(session: AsyncSession, *, team: Team, user: User, role: Role) -> Membership:
    membership = Membership(id=uuid.uuid4(), team_id=team.id, user_id=user.id, role=role.value)
    session.add(membership)
    await session.flush()
    return membership


async def create_work_item(
    session: AsyncSession,
    *,
    team: Team,
    created_by: User,
    title: str = DEFAULT_TITLE,
    assignee: User | None = None,
    status: Status = Status.OPEN,
    priority: Priority = Priority.MEDIUM,
    item_type: WorkItemType = WorkItemType.TASK,
    description: str = "",
) -> WorkItem:
    item = WorkItem(
        id=uuid.uuid4(),
        team_id=team.id,
        title=title,
        description=description,
        type=item_type.value,
        priority=priority.value,
        status=status.value,
        assignee_id=assignee.id if assignee else None,
        created_by=created_by.id,
        version=1,
    )
    session.add(item)
    await session.flush()
    return item


async def add_comment(
    session: AsyncSession, *, item: WorkItem, author: User, body: str = "A comment"
) -> Comment:
    comment = Comment(id=uuid.uuid4(), work_item_id=item.id, author_id=author.id, body=body)
    session.add(comment)
    await session.flush()
    return comment


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login(client: AsyncClient, email: str, password: str = TEST_PASSWORD) -> str:
    """Log in and return the bearer token, failing loudly if it does not work."""
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return str(response.json()["token"])


async def scenario(
    session: AsyncSession,
    *,
    roles: dict[str, Role],
    team_key: str = "alpha",
) -> tuple[Team, dict[str, User]]:
    """Create one team and one user per `roles` entry.

    ``roles`` maps an email local part to a role, for example
    ``{"lead": Role.LEAD, "member": Role.MEMBER}``. Returns the team and the users
    keyed by the same names.
    """
    team = await create_team(session, key=team_key)
    users: dict[str, User] = {}
    for name, role in roles.items():
        user = await create_user(session, email=f"{name}.{team_key}@newtonite.dev")
        await add_member(session, team=team, user=user, role=role)
        users[name] = user
    await session.commit()
    return team, users
