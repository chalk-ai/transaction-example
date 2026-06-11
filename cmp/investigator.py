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
    "Reply with APPROVE, DENY, or ESCALATE on the first line, "
    "then one sentence of reasoning. "
    "When you request features, request them in small numbers, and keep calling the tool for fetching features. "
    "We want to see interesting traces. "
    "If the user should be banned, ban the user."
)


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
    openai_client = get_openai_client()

    messages: list = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"User {user_id}. Refund reason: {reason!r}."},
    ]
    steps = []
    while True:
        response = openai_client.chat.completions.create(
            model="gpt-5.5",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_chalk_features",
                        "description": "Fetch some features for a user.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "user_id": {"type": "integer"},
                                "features": {
                                    "type": "array",
                                    "items": {
                                        "type": "string",
                                        "enum": [
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
                                        ],
                                    },
                                    "minItems": 1,
                                },
                            },
                            "required": ["user_id", "features"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "ban_user",
                        "description": "Ban a user",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "user_id": {"type": "integer"},
                            },
                        },
                        "required": ["user_id"],
                    },
                },
            ],
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

        for tc in msg.tool_calls:
            inp = json.loads(tc.function.arguments)
            with chalkcompute.span(f"tool.{tc.function.name}"):
                if tc.function.name == "get_chalk_features":
                    ctx = chalk_client.query(
                        input={"user.id": inp["user_id"]},
                        output=inp["features"],
                    )
                    result = "\n".join(f"{a.field}: {a.value}" for a in ctx.data)
                elif tc.function.name == "ban_user":
                    ban_user(inp["user_id"])
                else:
                    raise RuntimeError(f"unknown tool: {tc.function.name}")
            args = ", ".join(f"{k}={v!r}" for k, v in inp.items())
            steps.append(f"  {tc.function.name}({args}) → {result}")
            message = {"role": "tool", "tool_call_id": tc.id, "content": result}
            messages.append(message)



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