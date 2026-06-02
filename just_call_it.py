#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=1.5.17"]
# ///
from chalkcompute import RemoteFunction


if __name__ == "__main__":
    investigate = RemoteFunction.from_name("investigate_refund")
    print(investigate(1, "item not received"))

    # if "fanout" in sys.argv[1:]:
    #     run_fanout()
    # else:
    #     run_one()
