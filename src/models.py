from datetime import date, datetime
from enum import Enum

import chalk.functions as F
from chalk import (
    DataFrame,
    FeatureTime,
    NamedQuery,
    Primary,
    Windowed,
    _,
    feature,
    windowed,
)
from chalk.features import features, Vector


@features
class Transaction:
    id: int
    amount: float
    memo: str

    # The User.id type defines our join key implicitly
    user_id: "User.id"
    user: "User"

    # The time at which the transaction was created for temporal consistency
    at: FeatureTime


class TradelineKind(str, Enum):
    card = "card"
    auto = "auto"
    mortgage = "mortgage"
    student = "student"
    personal = "personal"
    other = "other"


@features
class Tradeline:
    id: int

    report_id: "CreditReport.id"

    opened_at: datetime
    closed_at: datetime | None

    kind: TradelineKind

    # The outstanding balance on the tradeline
    balance: float

    # The initial amount of the tradeline
    amount: float

    utilization_ratio: float = _.balance / _.amount

    # The amount past due on the tradeline
    amount_past_due: float

    # The monthly payment amount
    payment_amount: float

    payments: "DataFrame[Payment]"


class PaymentStatus(str, Enum):
    completed = "completed"
    pending = "pending"
    late = "late"
    failed = "failed"


class PaymentMethod(str, Enum):
    auto_pay = "auto_pay"
    manual = "manual"
    bank_transfer = "bank_transfer"
    credit_card = "credit_card"
    check = "check"


@features
class Payment:
    id: Primary[int]

    user_id: "User.id"
    user: "User"

    credit_report_id: "CreditReport.id"

    tradeline_id: "Tradeline.id"
    tradeline: "Tradeline"

    amount: float
    payment_date: datetime
    due_date: datetime | None
    payment_status: PaymentStatus
    payment_method: PaymentMethod | None

    created_at: datetime

    # Whether the payment was late
    is_late: bool = _.payment_status == "late"


@features
class CreditReport:
    id: int

    # The raw JSON of the credit report
    raw: str

    score: int = feature(min=300, max=850, strict=True)

    payments: DataFrame[Payment]

    late_payments: Windowed[int] = windowed(
        "7d",
        "30d",
        "all",
        expression=_.payments[
            _.created_at >= _.chalk_window,
            _.created_at <= _.chalk_now,
        ].count(),
        # materialization={"bucket_duration": "1h"},
    )

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
    name: str
    dob: date

    # calling a curated model through chalk
    email_embedding: Vector[1024] = F.catalog_call("model.qwen3", _.email)

    # calling a custom model through chalk
    model_score: float = F.catalog_call(
        "model.my-custom-model", _.name_email_match_score
    )

    # calling agents through chalk
    refund: bool = F.catalog_call(
        "model.investigate-refund", _.user_id, _.refund_reason
    )

    # calling llms through chalk router
    chalk_router_result: str = (
        F.openai_complete(prompt=_.x, model="anthropic/claude-sonnet-4-5-20250929")
        .with_rate_limit(rate=3, key="key")
        .completion
    )

    # calling sagemaker models
    model_score_2: bool = F.sagemaker_predict(...)

    email_username: str
    domain_name: str

    # Whether the user appears in a denylist in s3
    denylisted: bool

    # import chalk.functions as F
    name_email_match_score: float = F.partial_ratio(
        F.lower(_.name),
        F.lower(_.email_username),
    )

    emailage_response: str
    email_age_days: int
    domain_age_days: int

    credit_report_id: CreditReport.id
    credit_report: CreditReport

    # The transactions, linked by the User.id type on the Transaction.user_id field
    transactions: DataFrame[Transaction]

    total_spend: float = _.transactions[_.amount].sum()

    # The number of transfers made by the user in the
    # last 1, 7, and 30 days.
    count_withdrawals: Windowed[int] = windowed(
        "1d",
        "7d",
        "30d",
        "365d",
        expression=_.transactions[
            _.at >= _.chalk_window,
            _.amount < 0,
        ].count(),
        # materialization={"bucket_duration": "1h"},
    )

    is_fraud: bool

    # Shortest number of hops from this user to any known FraudCase node
    # in the Neptune identity-linkage graph, capped at 6.
    hops_to_known_fraud: int | None


NamedQuery(
    name="fraud_model",
    version="1.0.0",
    input=[User.id],
    output=[
        User.id,
        User.email,
        User.name,
        User.dob,
        User.email_username,
        User.domain_name,
        User.denylisted,
        User.name_email_match_score,
        User.emailage_response,
        User.email_age_days,
        User.domain_age_days,
        User.credit_report_id,
        User.total_spend,
        User.count_withdrawals,
        User.is_fraud,
    ],
)
