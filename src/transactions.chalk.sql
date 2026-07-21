-- resolves: Transaction
-- source: postgres
select
    id,
    amount,
    user_id,
    at,
    memo,
    direction,
    transaction_type,
    category,
    merchant,
    counterparty,
    status,
    return_code
from txns_demo
