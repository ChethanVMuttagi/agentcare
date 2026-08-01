"""Property-based test (Sprint 3, `hypothesis`) for JWT round-tripping —
complements the fixed cases in `tests/auth/test_jwt.py` by checking
`create_access_token`/`decode_access_token` agree on the subject for
ARBITRARY user ids and expiry windows, not just the ones a human picked.

`tests/auth/conftest.py`'s autouse fixture supplies a synthetic
`JWT_SECRET_KEY` for every test in this package.
"""

from __future__ import annotations

import uuid

from hypothesis import given
from hypothesis import strategies as st

from app.auth.jwt import create_access_token, decode_access_token
from app.core.config import get_settings


@given(
    user_id=st.uuids(),
    expire_minutes=st.integers(min_value=1, max_value=60 * 24 * 7),
)
def test_create_and_decode_round_trips_to_the_same_user_id(
    user_id: uuid.UUID, expire_minutes: int
) -> None:
    settings = get_settings().model_copy(
        update={"jwt_access_token_expire_minutes": expire_minutes}
    )

    token = create_access_token(user_id, settings)
    claims = decode_access_token(token, settings)

    assert claims.sub == str(user_id)
    assert claims.exp > claims.iat
