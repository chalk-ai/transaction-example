import json
from datetime import date

# from openai import OpenAI
from chalk import DataFrame, FeatureTime, Windowed, _, feature, windowed
from chalk.features import features

default_completion = json.dumps(
    dict(
        category="unknown",
        is_nsf=False,
        is_ach=False,
        clean_memo="",
    )
)

@features
class Transaction:
    id: int
    amount: float
    memo: str

    # :tags: genai
    clean_memo: str

    user_id: "User.id"
    user: "User"
    at: FeatureTime

    # :tags: genai
    completion: str = feature(max_staleness="infinity", default=default_completion)

    # :tags: genai
    category: str = "unknown"

    # :tags: genai
    is_nsf: bool = False
    # :tags: genai
    is_ach: bool = False


@features
class User:
    id: int
    email: str
    name: str
    dob: date
    # name: str
    is_high_risk: bool = _.count_transactions["1d"] > 10
    transactions: DataFrame[Transaction]
    count_transactions: Windowed[int] = windowed(
        "1d", "7d", "30d",
        expression=_.transactions[
            _.amount,
            _.at >= _.chalk_window,
            _.category == "Food"
        ].count(),
    )
    amount_transactions: Windowed[int] = windowed(
        "1d", "30d",
        expression=_.transactions[_.amount, _.at > _.chalk_window].sum(),
    )
