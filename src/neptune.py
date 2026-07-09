"""Neptune-backed resolvers for the identity-linkage graph features.

This file is excluded from `chalk apply` via `.chalkignore` — it's kept in
the repo as the reference implementation that would run in an environment
with a Neptune cluster available. The stubs in `src/neptune_stub.py`
define resolvers with the same names for the actual deploy.
"""

from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __

from chalk import online

from src.models import User

NEPTUNE_ENDPOINT = "fraud-graph.cluster-abcdef012345.us-west-2.neptune.amazonaws.com"
NEPTUNE_PORT = 8182
MAX_HOPS = 6


conn = DriverRemoteConnection(
    f"wss://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/gremlin",
    "g",
)

@online
def hops_to_known_fraud(user_id: User.id) -> User.hops_to_known_fraud:
    """Traverse the Neptune identity graph outward from this user along
    edges that commonly connect identities in fraud rings, and return the
    shortest number of hops to any known FraudCase node (capped).
    """

    g = traversal().withRemote(conn)

    # Start from this user, fan out across the identity/linkage edges
    # used for fraud detection, and stop as soon as a FraudCase is hit.
    # simplePath() prevents cycles; times(MAX_HOPS) caps traversal cost.
    paths = (
        g.V()
        .has("User", "user_id", str(user_id))
        .repeat(
            __.bothE(
                "USED_EMAIL",
                "USED_EMAIL_DOMAIN",
                "USED_DEVICE",
                "LOGGED_IN_FROM_IP",
                "SHARED_CREDIT_REPORT",
                "SHARED_TRADELINE",
                "SENT_TRANSACTION",
                "RECEIVED_TRANSACTION",
            )
            .otherV()
            .simplePath()
        )
        .until(__.hasLabel("FraudCase"))
        .emit(__.hasLabel("FraudCase"))
        .times(MAX_HOPS)
        .path()
        .toList()
    )

    if not paths:
        return None

    # A Gremlin Path's length counts vertices + edges; number of hops is
    # (len - 1) / 2. Take the shortest.
    return min((len(p) - 1) // 2 for p in paths)


@online
def linked_account_ids(user_id: User.id) -> User.linked_account_ids:
    """Collect other User accounts within two hops of this user along the
    identity-linkage edges (shared device, IP, email domain, or payment
    instrument)."""

    g = traversal().withRemote(conn)

    linked = (
        g.V()
        .has("User", "user_id", str(user_id))
        .repeat(
            __.bothE(
                "USED_EMAIL_DOMAIN",
                "USED_DEVICE",
                "LOGGED_IN_FROM_IP",
                "SHARED_CREDIT_REPORT",
            )
            .otherV()
            .simplePath()
        )
        .times(2)
        .emit(__.hasLabel("User"))
        .values("user_id")
        .dedup()
        .toList()
    )

    return sorted(int(uid) for uid in linked if int(uid) != user_id)
