-- resolves: Payment
-- source: postgres
select
    id,
    user_id,
    credit_report_id,
    tradeline_id,
    amount,
    payment_date,
    due_date,
    payment_status,
    payment_method,
    created_at
from payments
