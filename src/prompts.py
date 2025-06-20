from pydantic import BaseModel
from typing import Literal


class StructuredOutput(BaseModel):
    financial_stability: Literal["good", "average", "poor"]
    requires_manual_review: bool


SYSTEM_PROMPT: str = """
You are a financial risk analysis assistant. Your role is to  assess financial stability based on structured user and credit report inputs.

Your response MUST be a valid JSON object matching the following schema:
{
  "financial_stability": "good|average|poor",
  "requires_manual_review": true|false,
}

Analysis Guidelines:
1. Financial Stability Assessment:
   - Classify based on percent past due:
     * Good: 0-5%
     * Average: 6-20%
     * Poor: >20%
   - Analyze payment history and credit utilization

Be objective and data-driven. If information is missing, state your assumptions.
"""

USER_PROMPT: str = """
Analyze the financial stability of a user based on the following inputs:

{{User.dob}}: date of birth
{{User.denylisted}}: denylisted status
{{User.name_email_match_score}}: name-email match score
{{User.emailage_response}}: email age response
{{User.email_age_days}}: number of days since the email was created
{{User.domain_age_days}}: number of days since the domain was registered
{{User.credit_report_id}}: credit report ID
A detailed credit report including:
    - {{User.credit_report.num_tradelines}}: the number of tradelines
    - {{User.credit_report.total_balance}}: the total balance across all tradelines
    - {{User.credit_report.total_amount}}: the total amount across all tradelines
    - {{User.credit_report.percent_past_due}}: the percentage of overdue payments
    - {{User.credit_report.total_payment_amount}}: the total payment amount across all tradelines

The financial stability evaluation should:
1. Assess the financial stability of the user by analyzing their credit report data:
   - Compute {{User.credit_report.percent_past_due}} and categorize the user's credit health as Good, Average, or Poor.
   - Calculate their total financial obligations ({{User.credit_report.total_balance}} and {{User.credit_report.total_amount}}) to understand the scale of their liabilities.
   - Analyze the user's payment history ({{User.credit_report.total_payment_amount}}) to determine their repayment behavior.
"""
