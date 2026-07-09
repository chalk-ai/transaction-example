#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1", "openai", "pandas"]
# ///

import json
from datetime import datetime, timedelta

import chalkcompute
from chalkcompute import Image, Secret

SYSTEM_PROMPT = (
    "You investigate refund claims for potential abuse. "
    "You have access to real-time signals from the Chalk feature store — "
    "relevant tools include the fraud prediction and account risk signals. "
    "Use your tools to gather the evidence you need — look up the fraud prediction first, "
    "then decide whether you need more context before ruling. "
    "Fraud rarely acts alone — always check the user's linked accounts, "
    "and if any exist, investigate them with the same tool before ruling. "
    "Direct links to denylisted or fraudulent accounts are strong evidence of abuse. "
    "Reply with APPROVE, DENY, or ESCALATE on the first line, "
    "then one sentence of reasoning. "
    "When you request features, request them in small numbers, and keep calling the tool for fetching features. "
    "We want to see interesting traces. "
    "If the user should be banned, ban the user."
)

FEATURE_NAMES = [
    "user.email",
    "user.name",
    "user.dob",
    "user.email_username",
    "user.domain_name",
    "user.denylisted",
    "user.name_email_match_score",
    "user.emailage_response",
    "user.email_age_days",
    "user.domain_age_days",
    "user.credit_report_id",
    "user.total_spend",
    "user.count_withdrawals",
    "user.is_fraud",
    "user.hops_to_known_fraud",
    "user.linked_account_ids",
]


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOLS = [
    _tool(
        "get_chalk_features",
        "Fetch features for any user by id — including linked accounts discovered during the investigation.",
        {
            "user_id": {"type": "integer"},
            "features": {
                "type": "array",
                "items": {"type": "string", "enum": FEATURE_NAMES},
                "minItems": 1,
            },
        },
        ["user_id", "features"],
    ),
    _tool(
        "ban_user",
        "Ban a user",
        {"user_id": {"type": "integer"}},
        ["user_id"],
    ),
]


def run_agent(
    openai_client, messages: list, handlers: dict, model: str = "gpt-5.5"
) -> str:
    """Drive the chat-completion tool loop until the model stops calling tools.

    `handlers` maps a tool name to a callable taking the parsed arguments dict
    and returning a string result. Returns the trace of tool calls followed by
    the model's final message.
    """
    steps = []
    while True:
        response = openai_client.chat.completions.create(
            model=model,
            tools=TOOLS,
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
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )

        for tc in msg.tool_calls:
            inp = json.loads(tc.function.arguments)
            handler = handlers.get(tc.function.name)
            if handler is None:
                raise RuntimeError(f"unknown tool: {tc.function.name}")
            with chalkcompute.span(f"tool.{tc.function.name}"):
                result = handler(inp)
            args = ", ".join(f"{k}={v!r}" for k, v in inp.items())
            steps.append(f"  {tc.function.name}({args}) → {result}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})


@chalkcompute.function
def bulk_investigate_refunds() -> list[str]:
    return chalkcompute.gather(
        [
            (
                investigate_refund.with_knowledge_cutoff(
                    knowledge_cutoff=datetime.now() - timedelta(hours=24),
                ).defer(
                    uid,
                    "refund requested",
                )
            )
            for uid in get_users()
        ]
    )


@chalkcompute.function
def ban_user(user_id: int) -> int:
    return exec_sql(
        "update users set banned=1 where id=?",
        (user_id,),
    )


@chalkcompute.function(
    secrets=[
        Secret.from_env("OPENAI_API_KEY"),
        Secret.from_env("CHALK_CLIENT_ID"),
        Secret.from_env("CHALK_CLIENT_SECRET"),
        Secret.from_env("CHALK_ENVIRONMENT_ID"),
    ],
    image=Image.debian_slim(python_version="3.12").pip_install(
        [
            "chalkpy>=2.130.5",
            "openai",
            "opentelemetry-instrumentation-httpx",
        ]
    ),
)
def investigate_refund(user_id: int, reason: str) -> str:
    from chalk.client import ChalkClient

    chalk_client = ChalkClient()

    def get_chalk_features(inp: dict) -> str:
        ctx = chalk_client.query(
            input={"user.id": inp["user_id"]},
            output=inp["features"],
        )
        return "\n".join(f"{a.field}: {a.value}" for a in ctx.data)

    def ban(inp: dict) -> str:
        ban_user(inp["user_id"])
        return f"banned user {inp['user_id']}"

    messages: list = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"User {user_id}. Refund reason: {reason!r}."},
    ]

    return run_agent(
        get_openai_client(),
        messages,
        handlers={"get_chalk_features": get_chalk_features, "ban_user": ban},
    )


def get_openai_client():
    import httpx
    from openai import DefaultHttpxClient, OpenAI
    from opentelemetry.instrumentation.httpx import SyncOpenTelemetryTransport

    return OpenAI(
        base_url="https://chalk-router.tail0de09.ts.net/v1",
        api_key="ck-0502e75c81f8405c8f38bc7a4d54f291",
        http_client=DefaultHttpxClient(
            transport=SyncOpenTelemetryTransport(httpx.HTTPTransport()),
        ),
        max_retries=10,
    )


def exec_sql(*args):
    return 1


def get_users():
    return [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
