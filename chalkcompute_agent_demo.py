#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=1.5.17", "openai", "pandas"]
# ///
"""Refund-abuse investigation agent — Snowflake Summit demo.

Four things to notice in this file:

  1. SPRINKLE CHALK ON YOUR AGENT — one decorator, one entry point.
  2. SECRETS — the agent never holds the LLM key. Chalk injects it.
  3. CONTEXT ENGINE — tool calls pull real-time features from your store,
     inside your VPC, no data leaves.
  4. BRING YOUR OWN MODEL — swap Option A (hosted API) for Option B
     (self-hosted on Chalk Compute, see chalkcompute_vllm_server.py).
     Nothing — prompt, response, or weights — ever leaves your cloud.

Setup (once):
  - Deploy the features:   chalk apply
  - Local .env:            CHALK_API_SERVER, CHALK_CLIENT_ID,
                           CHALK_CLIENT_SECRET, CHALK_ENVIRONMENT(_ID),
                           VLLM_URL (from chalkcompute_vllm_server.py)

Run:
  ./chalkcompute_agent_demo.py            # investigate one order
  ./chalkcompute_agent_demo.py fanout     # fan out across 50 orders
"""

import os

import chalkcompute
from chalkcompute import Image, Secret

SYSTEM_PROMPT = (
    "You investigate refund claims for potential abuse. "
    "You have access to real-time signals from the Chalk feature store — "
    "relevant tools include the fraud prediction and account risk signals. "
    "Use your tools to gather the evidence you need — look up the fraud prediction first, "
    "then decide whether you need more context before ruling. "
    "Reply with APPROVE, DENY, or ESCALATE on the first line, "
    "then one sentence of reasoning."
)


@chalkcompute.function()
def add_numbers(a: int, b: int) -> int:
    return a + b


@chalkcompute.function(
    secrets=[
        Secret.from_env("OPENAI_API_KEY"),
        Secret.from_env("CHALK_CLIENT_ID"),
        Secret.from_env("CHALK_CLIENT_SECRET"),
        Secret.from_env("CHALK_ENVIRONMENT_ID"),
    ],
    image=Image.debian_slim(python_version="3.12").pip_install(["chalkpy>=2.130.5", "openai"]),
)
def investigate_refund(user_id: int, reason: str) -> str:
    from chalk.client import ChalkClient
    from openai import OpenAI

    client = OpenAI()

    # ── Tools: the agent decides which to call and in what order ──────────────
    # TODO: Replace this with a call to the MCP Gateway
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_chalk_features",
                "description": (
                    """Fetch some features. The features available to you are:
                    user.email,
                    user.name,
                    user.dob,
                    user.email_username,
                    user.domain_name,
                    user.denylisted,
                    user.name_email_match_score,
                    user.emailage_response,
                    user.email_age_days,
                    user.domain_age_days,
                    user.credit_report_id,
                    user.total_spend,
                    user.count_withdrawals,
                    user.is_fraud,
                    
                    request them in small numbers, and keep calling this method a bunch.
                    inputs should be a comma separated list
                    """
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"user_id": {"type": "integer"}, "features": {"type": "string"}},
                    "required": ["user_id", "features"],
                },
            },
        },
        # {
        #     "type": "function",
        #     "function": {
        #         "name": "get_fraud_prediction",
        #         "description": (
        #             "Fetch the real-time fraud prediction for a user from the Chalk "
        #             "fraud_model named query. Returns is_fraud (bool) and "
        #             "name_email_match_score (0–100, higher = better match)."
        #         ),
        #         "parameters": {
        #             "type": "object",
        #             "properties": {"user_id": {"type": "integer"}},
        #             "required": ["user_id"],
        #         },
        #     },
        # },
        # {
        #     "type": "function",
        #     "function": {
        #         "name": "get_account_risk",
        #         "description": (
        #             "Look up account risk signals for a user: whether they appear on a "
        #             "denylist, and how old their email address is in days."
        #         ),
        #         "parameters": {
        #             "type": "object",
        #             "properties": {"user_id": {"type": "integer"}},
        #             "required": ["user_id"],
        #         },
        #     },
        # },
    ]

    # add = RemoteFunction.from_name("add_numbers")
    # return c(3, 5) + c(3, 5) + c(3, 5)

    # ── Context engine: one line each, runs in your VPC, no data leaves ───────
    def run_tool(name: str, inp: dict) -> str:
        import traceback as _tb

        try:
            uid = int(inp["user_id"])
            if name == "get_chalk_features":
                ctx = ChalkClient(
                    # client_id=os.getenv("CHALK_CLIENT_ID"),
                    # client_secret=os.getenv("CHALK_CLIENT_SECRET"),
                    # trace=True
                ).query(
                    input={"user.id": uid},
                    output=inp["features"].split(","),
                )
                return "\n".join(f"{a.field}: {a.value}" for a in ctx.data)
        except Exception as e:
            return f"error: {type(e).__name__}: {e}\n{_tb.format_exc()}"
        return "unkonwn command!"

    # ── Agentic tool-use loop ─────────────────────────────────────────────────
    messages: list = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"User {user_id}. Refund reason: {reason!r}."},
    ]
    steps: list[str] = []

    while True:
        response = client.chat.completions.create(
            model="gpt-5.5",
            # max_tokens=1024,
            tools=tools,
            messages=messages,
        )

        msg = response.choices[0].message

        if not msg.tool_calls:
            decision = msg.content or ""
            trace = "\n".join(steps)
            return f"{trace}\n\n{decision}".lstrip() if steps else decision

        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        import json

        for tc in msg.tool_calls:
            inp = json.loads(tc.function.arguments)
            result = run_tool(tc.function.name, inp)
            args = ", ".join(f"{k}={v!r}" for k, v in inp.items())
            steps.append(f"  {tc.function.name}({args}) → {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


_DEMO_USERS = {
    "1": (1, "high risk — flagged account"),
    "2": (2, "low risk — clean account"),
    "3": (3, "medium risk — new email"),
}


def run_one() -> None:
    """Scene — investigate a single refund."""
    # print("\nSelect a user:")
    # for key, (user_id, description) in _DEMO_USERS.items():
    #     print(f"  {key}. user_id={user_id}  ({description})")
    # choice = input("\nUser [1]: ").strip() or "1"
    choice = "1"
    user_id, _ = _DEMO_USERS.get(choice, _DEMO_USERS["1"])

    # reason = input("Refund reason: ").strip() or "Item arrived damaged"
    reason = "Item arrived damaged"

    print()
    try:
        print(investigate_refund(user_id, reason))
    except RuntimeError as e:
        if "503" in str(e):
            print("Error: vLLM server unavailable (503). Re-run ./chalkcompute_vllm_server.py to restart it.")
        else:
            raise


def run_fanout(n: int = 50) -> None:
    """Scene — fan out across N historical orders, concurrently."""
    import re
    import time
    from concurrent.futures import ThreadPoolExecutor

    import pandas as pd

    @pd.api.extensions.register_dataframe_accessor("chalk")
    class _ChalkAccessor:
        def __init__(self, df: pd.DataFrame):
            self._df = df

        def apply(self, fn) -> list:
            with ThreadPoolExecutor(max_workers=len(self._df)) as ex:
                return list(ex.map(lambda r: fn(*r[1:]), self._df.itertuples()))

    known = [1, 2, 3]
    reasons = [
        "Item arrived damaged",
        "Wrong item received",
        "Item not as described",
        "Quality issue",
        "Item never delivered",
    ]
    orders = pd.DataFrame(
        {
            "user_id": [known[i % len(known)] if i < 30 else (100 + i) for i in range(n)],
            "reason": [reasons[i % len(reasons)] for i in range(n)],
        }
    )
    print(f"\nFan-out: {len(orders)} users\n")

    # ── ONE LINE: fan out N concurrent agent invocations ──
    t0 = time.time()
    try:
        decisions = orders.chalk.apply(investigate_refund)
    except RuntimeError as e:
        if "503" in str(e):
            print("Error: vLLM server unavailable (503). Re-run ./chalkcompute_vllm_server.py to restart it.")
            return
        raise
    elapsed = time.time() - t0

    def _verdict(text: str) -> str:
        m = re.search(r"\b(APPROVE|DENY|ESCALATE)\b", text)
        return m.group(0) if m else text.split("\n")[0].strip()

    orders["decision"] = [_verdict(d) for d in decisions]
    approve = orders["decision"].eq("APPROVE").sum()
    deny = orders["decision"].eq("DENY").sum()
    escalate = orders["decision"].eq("ESCALATE").sum()
    print(orders[["user_id", "reason", "decision"]].to_string(index=False))
    print(
        f"\n{len(orders)} agents finished in {elapsed:.1f}s "
        f"(~{elapsed / len(orders) * 1000:.0f}ms/agent average) — "
        f"{approve} APPROVE, {deny} DENY, {escalate} ESCALATE"
    )


if __name__ == "__main__":
    run_one()

    # if "fanout" in sys.argv[1:]:
    #     run_fanout()
    # else:
    #     run_one()
