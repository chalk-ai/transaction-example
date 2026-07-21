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
    direction: str
    transaction_type: str
    category: str
    merchant: str
    counterparty: str
    status: str
    return_code: str | None

    # The User.id type defines our join key implicitly
    user_id: "User.id"
    user: "User"

    is_fraud: bool

    # The time at which the transaction was created for temporal consistency
    at: FeatureTime

    # Derived fraud signals
    is_small: bool = _.amount < 5.0
    is_return: bool = _.status == "RETURNED"
    is_night: bool = F.hour_of_day(_.at) < 6
    memo_length: int = F.length(_.memo)


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
    dob: date = feature(default=date(1970, 1, 1))


    # NOTE: the features below are illustrative — they reference model-catalog
    # entries and inputs that don't exist in this environment, so they are
    # commented out to keep `chalk apply` deployable.

    # calling a curated model through chalk
    # email_embedding: Vector[1024] = F.catalog_call("model.qwen3", _.email)

    # calling a custom model through chalk
    # model_score: float = F.catalog_call(
    #     "model.my-custom-model", _.name_email_match_score
    # )

    # calling agents through chalk
    # refund: bool = F.catalog_call(
    #     "model.investigate-refund", _.user_id, _.refund_reason
    # )

    # calling llms through chalk router
    # chalk_router_result: str = (
    #     F.openai_complete(prompt=_.x, model="anthropic/claude-sonnet-4-5-20250929")
    #     .with_rate_limit(rate=3, key="key")
    #     .completion
    # )

    # calling sagemaker models
    # model_score_2: bool = F.sagemaker_predict(...)

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


    txn_count: int = _.transactions.count()
    total_debit_amount: float = _.transactions[_.direction == "debit", _.amount].sum()
    total_credit_amount: float = _.transactions[_.direction == "credit", _.amount].sum()
    avg_txn_amount: float = _.transactions[_.amount].mean()
    max_txn_amount: float = _.transactions[_.amount].max()

    small_txn_count: int = _.transactions[_.amount < 5.0].count()
    night_txn_count: int = _.transactions[F.hour_of_day(_.at) < 6].count()
    ach_return_count: int = _.transactions[_.status == "RETURNED"].count()
    large_transfer_count: int = _.transactions[
        _.category == "transfer", _.amount > 1500.0
    ].count()
    p2p_credit_count: int = _.transactions[
        _.transaction_type == "P2P", _.direction == "credit"
    ].count()


    small_txn_ratio: float = F.if_then_else(
        _.txn_count > 0, _.small_txn_count / _.txn_count, 0.0
    )
    night_txn_ratio: float = F.if_then_else(
        _.txn_count > 0, _.night_txn_count / _.txn_count, 0.0
    )
    ach_return_ratio: float = F.if_then_else(
        _.txn_count > 0, _.ach_return_count / _.txn_count, 0.0
    )
    debit_credit_ratio: float = F.if_then_else(
        _.total_credit_amount > 0.0,
        _.total_debit_amount / _.total_credit_amount,
        _.total_debit_amount,
    )

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

    # Other accounts connected to this user in the Neptune identity-linkage
    # graph (shared device, IP, email domain, or payment instrument).
    linked_account_ids: list[int] | None


NamedQuery(
    name="fraud-model-data",
    version="1.0.0",
    input=[User.id],
    output=[
        User.is_fraud,
        User.txn_count,
        User.avg_txn_amount,
        User.night_txn_count,
        User.large_transfer_count,
        User.p2p_credit_count,
    ],
)
