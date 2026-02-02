from models import User
from chalk import make_model_resolver
from chalk.ml import ModelReference


fraud_detection_model = ModelReference.from_version(
    name="fraud_detection_model", version=1
)

_ = make_model_resolver(
    name="fraud_detection_model",
    model=fraud_detection_model,
    input=[
        User.denylisted,
        User.email_age_days,
        User.domain_age_days,
        User.name_email_match_score,
        User.credit_report.percent_past_due,
        User.count_withdrawals__86400__,
        User.count_withdrawals__604800__,
        User.total_spend,
    ],
    output=User.is_fraud,
)
