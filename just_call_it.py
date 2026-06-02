#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1"]
# ///
from chalkcompute import RemoteFunction


if __name__ == "__main__":
    # print(RemoteFunction.from_name("bulk_investigate_refunds").remote(3))

    investigate = RemoteFunction.from_name("investigate_refund")
    print(investigate.remote(1, "item not received"))

    # for x in investigate(1, "item not received"):
    #     print(x)

    # for x in :
    #     print(x)
    # print()

    # if "fanout" in sys.argv[1:]:
    #     run_fanout()
    # else:
    #     run_one()
