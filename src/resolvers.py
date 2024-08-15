import json
import textwrap

import google.generativeai as genai
from chalk import DataFrame, clogging, online
from chalk.features import Features

from src.models import Transaction

model = genai.GenerativeModel(model_name="models/gemini-1.5-flash-latest")


@online
async def get_transaction_category(memo: Transaction.memo) -> Transaction.completion:
    return model.generate_content(
        textwrap.dedent(
            f"""\
        Please return JSON for classifying a financial transaction
        using the following schema.
        {{"category": str, "is_nsf": bool, "clean_memo": str, "is_ach": bool}}
        All fields are required. Return EXACTLY one JSON object with NO other text.
        Memo: {memo}"""
        ),
        generation_config={"response_mime_type": "application/json"},
    ).candidates[0].content.parts[0].text


@online
def get_structured_outputs(completion: Transaction.completion) -> Features[
    Transaction.category,
    Transaction.is_nsf,
    Transaction.is_ach,
    Transaction.clean_memo,
]:
    body = json.loads(completion)
    return Transaction(
        category=body["category"],
        is_nsf=body["is_nsf"],
        is_ach=body["is_ach"],
        clean_memo=body["clean_memo"],
    )
