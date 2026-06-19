"""Generator client — the streaming `investigate_refund_streaming`.

Same idea as the chunked client, but the deployed function is a generator: we
`yield from` its `.remote(...)` so chunks pass straight through as they arrive,
rather than being joined into one string up front.
"""

from typing import Iterator

from chalkcompute import RemoteFunction
from dotenv import load_dotenv

load_dotenv()  # reads .env for Chalk credentials

# resolve deployed agent by name — no URL, no client wiring
agent = RemoteFunction.from_name("investigate_refund_streaming")

# call the agent — runs server-side in Chalk Compute, we yield chunks as they arrive
def investigate(user_id: int, reason: str) -> Iterator[str]:
    yield from agent.remote(user_id, reason)
