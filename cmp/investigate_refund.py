#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1"]
# ///
from chalkcompute import RemoteFunction
user_id = 1
reason = "item not received"

if __name__ == "__main__":
    investigate = RemoteFunction.from_name("investigate_refund")
    print(investigate.remote(user_id, reason))
