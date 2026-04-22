"""Stub resolver for `User.hops_to_known_fraud`.

The real implementation lives in `src/neptune.py` and queries a Neptune
cluster. That file is `.chalkignore`d so it doesn't deploy; this file
defines a same-named resolver that returns a constant so the feature
resolves in environments without graph DB access.
"""

from chalk import online

from src.models import User


@online
def hops_to_known_fraud(user_id: User.id) -> User.hops_to_known_fraud:
    return 5
