"""Stub resolvers for the Neptune-backed graph features.

The real implementations live in `src/neptune.py` and query a Neptune
cluster. That file is `.chalkignore`d so it doesn't deploy; this file
defines same-named resolvers that return fixed values so the features
resolve in environments without graph DB access.
"""

from chalk import online

from src.models import User

# user_id -> shortest hops to a known FraudCase node in the identity graph
HOPS_TO_KNOWN_FRAUD = {17: 1}

# user_id -> accounts sharing a device, IP, email domain, or payment instrument
LINKED_ACCOUNTS = {1: [17, 23]}


@online
def hops_to_known_fraud(user_id: User.id) -> User.hops_to_known_fraud:
    return HOPS_TO_KNOWN_FRAUD.get(user_id, 5)


@online
def linked_account_ids(user_id: User.id) -> User.linked_account_ids:
    return LINKED_ACCOUNTS.get(user_id, [])
