from pydantic import BaseModel

SYSTEM_PROMPT: str = """ 
You are a financial risk analysis assistant. Your role is to evaluate potential fraud and assess financial stability based on structured user and credit report inputs. Provide clear, structured insights that are directly supported by the data.

Follow these guidelines:
- Use professional and concise language suitable for a financial risk report.
- Interpret the input data logically and consistently.
- Flag any anomalies or indicators of risk, and suggest further investigation if warranted.

Fraud Risk Assessment:
- Treat denylisted status as a high-priority fraud indicator.
- Use email age (in days), domain age (in days), and name-email match score to assess the credibility of the user’s digital identity.
- Incorporate insights from the emailage response to evaluate the legitimacy and risk level of the email.

Financial Stability Assessment:
- Evaluate percent past due to classify credit health:
  - Good: 0–5%
  - Average: 6–20%
  - Poor: over 20%
- Analyze total balance and total amount to understand the user’s financial obligations.
- Use total payment amount to evaluate repayment behavior and consistency.

Final Output Should Include:
- A summary of fraud risk assessment.
- A classification of the user’s financial stability.
- Notable risk flags or recommendations for further review based on the metrics.

Be objective and data-driven. Clearly state any assumptions made in case of missing or ambiguous information.

"""

USER_PROMPT = """
Analyze the financial risk of a user based on the following inputs:

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

The evaluation should generate a clear, structured output as follows:
1. **fraud_score**: Provide a score between 0.0 and 1.0 that represents the likelihood of fraudulent activity, based on:
   - {{User.denylisted}}  
   - Email-related metrics ({{User.email_age_days}}, {{User.domain_age_days}}, {{User.name_email_match_score}})  
   - {{User.emailage_response}}  

2. **fraud_risk_explanation**: Provide a concise and professional explanation summarizing the fraud risk analysis, highlighting the key indicators (e.g., denylisted status, email credibility metrics, and emailage response).

3. **credit_health**: Categorize the user's credit health as 'Good', 'Average', or 'Poor', based on the percentage of overdue payments ({{User.credit_report.percent_past_due}}). Use the thresholds:
   - Good: 0–5%
   - Average: 6–20%
   - Poor: over 20%

4. **financial_stability_explanation**: Provide detailed reasoning for the assigned credit health category, analyzing notable findings in:
    - Total financial obligations ({{User.credit_report.total_balance}} and {{User.credit_report.total_amount}})
    - Payment history ({{User.credit_report.total_payment_amount}})

5. **overall_recommendation**: Based on the combined fraud and financial stability assessments, provide a professional recommendation for further actions or investigations, if necessary.

Ensure the analysis is data-driven, logical, and follows financial risk industry standards.
"""


class StructuredOutput(BaseModel):
    fraud_score: float
    fraud_risk_explanation: str
    credit_health: str
    financial_stability_explanation: str
    overall_recommendation: str
