from src.models import User
from chalk import make_model_resolver
from chalk.ml import ModelReference


fraud_detection_model = ModelReference.from_version(
    name="fraud_detection_model", version=4
)

make_model_resolver(
    name="fraud_detection_model",
    model=fraud_detection_model,
    input=[
        User.denylisted,
        User.email_age_days,
        User.domain_age_days,
        User.name_email_match_score,
        User.credit_report.percent_past_due,
        User.count_withdrawals["1d"],
        User.count_withdrawals["7d"],
        User.total_spend,
    ],
    output=User.is_fraud,
)
