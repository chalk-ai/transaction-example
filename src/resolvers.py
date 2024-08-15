import json
from datetime import datetime, timedelta, timezone

# import google.generativeai as genai
from chalk import DataFrame, online
from chalk.features import Features

from src.models import Transaction


@online
def get_transactions() -> (
    DataFrame[
        Transaction.id,
        Transaction.user_id,
        Transaction.at,
        Transaction.amount,
        Transaction.memo,
    ]
):
    return DataFrame(
        [
            Transaction(
                id=1, amount=10.0, memo="Lunch", user_id=1, at=datetime.now(tz=timezone.utc) - timedelta(days=1)
            ),
            Transaction(id=2, amount=20.0, memo="Dinner", user_id=1, at=datetime.now(tz=timezone.utc) - timedelta(days=2)),
            Transaction(id=3, amount=30.0, memo="Breakfast", user_id=2, at=datetime.now(tz=timezone.utc) - timedelta(days=3)),
            Transaction(id=4, amount=40.0, memo="Lunch", user_id=2, at=datetime.now(tz=timezone.utc) - timedelta(days=4)),
            Transaction(id=5, amount=50.0, memo="Dinner", user_id=3, at=datetime.now(tz=timezone.utc) - timedelta(days=5)),
            Transaction(id=6, amount=60.0, memo="Breakfast", user_id=3, at=datetime.now(tz=timezone.utc) - timedelta(days=6)),
        ]
    )


# genai.configure(api_key="AIzaSyCEgFSw5mRj-POYuvhJJKhIfw76NJxaUo0")
# model = genai.GenerativeModel(model_name="models/gemini-1.5-flash-latest")


@online
def get_transaction_categories(
    transactions: DataFrame[
        Transaction.memo,
        Transaction.id,
    ],
) -> DataFrame[
    Transaction.completion,
    Transaction.id,
]:
    # response = model.generate_content(
    #     textwrap.dedent("""\
    #     Please return JSON with example transactions using the following schema:
    #
    #     {"memo": str, "amount": float, "datetime": datetime}
    #
    #     All fields are required. Return a list 100 of these transactions.
    #
    #     Important: Only return a single piece of valid JSON text.
    #     """),
    #     generation_config={"response_mime_type": "application/json"},
    # )
    # print(response)
    # json.loads(response.candidates[0].content.parts[0].text)
    # list(response.candidates)[0].content
    return transactions[Transaction.id].with_column(
        Transaction.completion,
        ['{"category": "nice"}'] * len(transactions),
    )


@online
def get_structured_outputs(
    completion: Transaction.completion
) -> Features[Transaction.category]:
    body = json.loads(completion)
    return Transaction(
        category=body["category"],
    )