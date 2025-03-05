import json
from datetime import date

import chalk.functions as F
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

    # The User.id type defines our join key implicitly
    user_id: "User.id"
    user: "User"

    name_memo_sim: float = F.contains(_.user.name, _.clean_memo)

    # The time at which the transaction was created for temporal consistency
    at: FeatureTime

    completion: str = feature(max_staleness="infinity", default=default_completion)

    category: str = "unknown"
    is_nsf: bool = False
    is_ach: bool = False


@features
class Tradeline:
    id: int
    report_id: "CreditReport.id"

    # The outstanding balance on the tradeline
    balance: float

    # The initial amount of the tradeline
    amount: float

    # The amount past due on the tradeline
    amount_past_due: float

    # The monthly payment amount
    payment_amount: float


@features
class CreditReport:
    id: int

    # The raw JSON of the credit report
    raw: str

    tradelines: DataFrame[Tradeline]

    num_tradelines: int = _.tradelines.count()
    total_balance: float = _.tradelines[_.balance].sum()
    total_amount: float = _.tradelines[_.amount].sum()
    percent_past_due: float = _.tradelines[_.amount_past_due].sum() / _.total_amount
    total_payment_amount: float = _.tradelines[_.payment_amount].sum()


@features
class User:
    # Features pulled from Postgres for the user
    id: int
    email: str
    domain_name: str
    name: str
    dob: date

    email_username: str

    # Whether the user appears in a denylist in s3
    denylisted: bool

    name_email_match_score: float

    emailage_response: str
    email_age_days: int
    domain_age_days: int

    credit_report_id: CreditReport.id
    credit_report: CreditReport

    # The transactions, linked by the User.id type on the Transaction.user_id field
    transactions: DataFrame[Transaction]

    # The number of food and drink purchases made by the user in the
    # last 1, 7, and 30 days.
    # Uses the category pulled from Gemini to count payments
    count_food_purchases: Windowed[int] = windowed(
        "1d",
        "7d",
        "30d",
        expression=_.transactions[
            _.at >= _.chalk_window,
            _.category == "Food & Drink",
        ].count(),
    )
