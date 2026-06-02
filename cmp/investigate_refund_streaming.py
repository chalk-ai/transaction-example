#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1"]
# ///
from chalkcompute import RemoteFunction


if __name__ == "__main__":
    investigate = RemoteFunction.from_name("investigate_refund_streaming")
    for m in investigate.remote(1, "item not received"):
        print(m)
