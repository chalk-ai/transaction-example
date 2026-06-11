#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1", "openai", "pandas"]
# ///


def exec_sql(*args):
    return 1


def get_users():
    return [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
























from datetime import datetime, timedelta

import chalkcompute
from chalkcompute import RemoteFunction

if __name__ == "__main__":
    investigate_refund = RemoteFunction.from_name("investigate_refund")
    chalkcompute.gather(
        [
            (
                investigate_refund.with_knowledge_cutoff(
                    knowledge_cutoff=datetime.now() - timedelta(hours=24),
                ).defer(uid, "refund requested")
            )
            for uid in get_users()
        ]
    )





















