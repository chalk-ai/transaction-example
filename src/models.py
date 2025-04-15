import json
from datetime import date

import chalk.functions as F
import chalk.prompts as P
from chalk import DataFrame, FeatureTime, Windowed, _, feature, windowed
from chalk.features import features

from .groq import GROQ_API_KEY
from .prompts import SYSTEM_PROMPT, USER_PROMPT, StructuredOutput

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

    name_memo_sim: float = F.jaccard_similarity(_.user.name, _.clean_memo)

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
    name: str
    dob: date

    email_username: str
    domain_name: str

    # Whether the user appears in a denylist in s3
    denylisted: bool

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
    count_transfers: Windowed[int] = windowed(
        "1d",
        "7d",
        "30d",
        expression=_.transactions[
            _.at >= _.chalk_window,
            _.category == "Transfer",
        ].count(),
    )

    llm: P.PromptResponse = P.completion(
        api_key=GROQ_API_KEY,
        model_provider="openai",
        model="llama3-8b-8192",
        base_url="https://api.groq.com/openai/v1",
        max_tokens=8192,
        temperature=0.1,
        top_p=0.1,
        messages=[
            P.message(
                role="system",
                content=SYSTEM_PROMPT,
            ),
            P.message(
                role="user",
                content=F.jinja(USER_PROMPT),
            ),
        ],
        output_structure=StructuredOutput,
    )
    llm_response: str = _.llm.response
    # Define variables based on the structured output

    # Path to fraud score in LLM's structured response
    llm_fraud_score: float = F.json_value(_.llm.response, "$.fraud_score")
    llm_fraud_risk_explanation: str = F.json_value(
        _.llm.response,
        "$.fraud_risk_explanation",
    )

    # Path to credit health categorization in LLM's structured response
    llm_credit_health: str = F.json_value(_.llm.response, "$.credit_health")

    # Path to financial stability explanation in LLM's structured response
    llm_financial_stability_explanation: str = F.json_value(
        _.llm.response,
        "$.financial_stability_explanation",
    )

    # Path to overall recommendation in LLM's structured response
    llm_overall_recommendation: str = F.json_value(
        _.llm.response,
        "$.overall_recommendation",
    )
