import json
from datetime import date, datetime
from enum import Enum

import chalk.functions as F
import chalk.prompts as P
from chalk.features import Vector, embed
from chalk import (
    DataFrame,
    FeatureTime,
    Windowed,
    _,
    feature,
    windowed,
    Primary,
    has_many,
    Validation,
)
from chalk.features import features

from .groq import GROQ_API_KEY, GROQ_BASE_URL, GROQ_MODEL, GROQ_MODEL_PROVIDER
from .prompts import SYSTEM_PROMPT, StructuredOutput, USER_PROMPT
from .transaction_search_result import TransactionSearchResult

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

    # import chalk.functions as F
    name_memo_sim: float = F.jaccard_similarity(
        _.user.name,
        _.clean_memo,
    )

    # Features can be vectors, and they can be computed
    # via services or hosted models
    vector: Vector[768] = embed(
        input=lambda: Transaction.memo,
        provider="vertexai",
        model="text-embedding-005",
        max_staleness="infinity",
    )

    # The time at which the transaction was created for temporal consistency
    at: FeatureTime

    completion: str = feature(max_staleness="infinity", default=default_completion)

    category: str = "unknown"
    is_nsf: bool = False
    is_ach: bool = False


@features
class TransactionSearch:
    q: Primary[str]
    limit: int = 25
    vector: Vector[768] = embed(
        input=lambda: TransactionSearch.q,
        provider="vertexai",  # openai
        model="text-embedding-005",  # text-embedding-3-small
    )

    results: DataFrame[TransactionSearchResult] = has_many(
        lambda: TransactionSearch.q == TransactionSearchResult.query
    )



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
        ].count()
        # materialization={"bucket_duration": "1h"},
    )

    tradelines: DataFrame[Tradeline]

    num_tradelines: int = _.tradelines.count()
    total_balance: float = _.tradelines[_.balance].sum()
    total_amount: float = _.tradelines[_.amount].sum()
    percent_past_due: float = _.tradelines[_.amount_past_due].sum() / _.total_amount
    total_payment_amount: float = _.tradelines[_.payment_amount].sum()


class FinancialStability(str, Enum):
    GOOD = "good"
    AVERAGE = "average"
    POOR = "poor"


@features
class User:
    # Features pulled from Postgres for the user
    id: int
    email: str
    name: str
    dob: date

    email_username: str
    domain_name: str

    # Whether the user appears in a denylist in s3
    denylisted: bool

    # import chalk.functions as F
    name_email_match_score: float = F.partial_ratio(
        _.name,
        _.email_username,
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
    # Uses the category pulled from Gemini to count payments
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

    llm: P.PromptResponse = feature(
        max_staleness="infinity",
        expression=P.completion(
            messages=[
                P.message(role="system", content=SYSTEM_PROMPT),
                P.message(
                    role="user",
                    content=F.jinja(USER_PROMPT),
                ),
            ],
            api_key=GROQ_API_KEY,
            model_provider=GROQ_MODEL_PROVIDER,
            model=GROQ_MODEL,
            base_url=GROQ_BASE_URL,
            max_tokens=8192,
            temperature=0.1,
            top_p=0.1,
            output_structure=StructuredOutput,  # can pass in a pydantic base model for structured output
        ),
    )
    llm_financial_stability: FinancialStability = feature(
        max_staleness="infinity",
        expression=F.json_value(
            _.llm.response,
            "$.financial_stability",
        ),
    )
    llm_requires_manual_review: bool = feature(
        max_staleness="infinity",
        expression=F.json_value(
            _.llm.response,
            "$.requires_manual_review",
        ),
    )
