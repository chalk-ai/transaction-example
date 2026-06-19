"""Chunked client — the buffered `investigate_refund`.

Resolve the deployed agent by name, call it, get its whole reply back as one
string. `.remote()` returns the full "{trace}\n\n{verdict}" text once the agent
finishes; `"".join(...)` is a no-op on a single string but also drains an
iterator, so this works whether the deploy returns a scalar or yields.
"""

from chalkcompute import RemoteFunction
from dotenv import load_dotenv

load_dotenv()  # reads .env for Chalk credentials

# resolve deployed agent by name — no URL, no client wiring
agent = RemoteFunction.from_name("investigate_refund")

# call the agent — runs server-side in Chalk Compute, we get text back
def investigate(user_id: int, reason: str) -> str:
    return "".join(agent.remote(user_id, reason))
