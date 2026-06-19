import json
from datetime import datetime

from chalk import online
from chalk.features import Features, before_all

from src.denylist import Denylist
from src.emailage.client import emailage_client
from src.experian import ExperianClient
from src.models import CreditReport, Tradeline, TradelineKind, User


@online
def predict_is_fraud(
    denylisted: User.denylisted,
    name_email_match_score: User.name_email_match_score,
    email_age_days: User.email_age_days,
    hops_to_known_fraud: User.hops_to_known_fraud,
) -> User.is_fraud:
    """A trivial rules-based fraud prediction from a few signals."""
    if denylisted:
        return True
    if hops_to_known_fraud is not None and hops_to_known_fraud <= 2:
        return True
    return name_email_match_score < 50 and email_age_days < 30


denylist = Denylist(source="gs://socure-data/denylist.csv")


@before_all
def init_denylist():
    denylist.load()


@online
def get_domain_name(email: User.email) -> User.domain_name:
    return email.split("@")[-1]


@online
def get_email_username(email: User.email) -> User.email_username:
    # def get_email_username(email: str) -> str:
    username = email.split("@")[0]
    if "gmail.com" in email:
        username = username.split("+")[0].replace(".", "")
    return username.lower()


@online
def email_in_denylist(
    email: User.email,
    username: User.email_username,
) -> User.denylisted:
    """Check if the user's email is in a fixed set of denylisted emails."""
    return email in denylist or username in denylist


@online
def get_email_age(email: User.email) -> User.emailage_response:
    """Get the email age and domain age from the Emailage API."""
    return emailage_client.get_email_score(email)


@online
def get_emailage_features(
    emailage_response: User.emailage_response,
) -> Features[User.email_age_days, User.domain_age_days]:
    """Parse the emailage response into the email and domain age."""
    parsed = json.loads(emailage_response)
    return User(
        email_age_days=parsed["emailAge"],
        domain_age_days=parsed["domainAge"],
    )


experian_client = ExperianClient("EXPERIAN_API_KEY")


@online
def get_credit_report(
    name: User.name,
    dob: User.dob,
) -> Features[User.credit_report.raw, User.credit_report.id]:
    """Fetch the credit report from Experian."""
    return experian_client.fetch_credit_report(name, dob)


@online
def get_tradelines(
    raw: CreditReport.raw,
) -> CreditReport.tradelines[
    Tradeline.id,
    Tradeline.balance,
    Tradeline.amount,
    Tradeline.amount_past_due,
    Tradeline.payment_amount,
    Tradeline.opened_at,
    Tradeline.closed_at,
    Tradeline.kind,
]:
    """Parse the raw credit report into tradelines."""
    parsed = json.loads(raw)
    return CreditReport(
        tradelines=[
            Tradeline(
                id=tradeline["Id"],
                balance=tradeline["Balance"],
                amount=tradeline["Amount"],
                amount_past_due=tradeline["AmountPastDue"],
                payment_amount=tradeline["PaymentAmount"],
                opened_at=datetime.fromisoformat(tradeline["OpenedAt"]),
                closed_at=datetime.fromisoformat(tradeline["ClosedAt"]) if tradeline.get("ClosedAt") else None,
                kind=TradelineKind(tradeline["Kind"]),
            )
            for tradeline in parsed["Tradelines"]
        ]
    )
