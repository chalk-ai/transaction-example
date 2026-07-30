from datetime import datetime

import pyarrow as pa
from chalk import functions as F
from chalk.features import _
from chalk.features.resolver import make_stream_resolver
from chalk.streams import KafkaSource
from pydantic import BaseModel

from src.models import Transaction

transactions_topic = KafkaSource(name="transactions")


class TransactionMessage(BaseModel):
    id: str
    memo: str
    amount: float
    at: datetime


process_stream_message = make_stream_resolver(
    name="process_stream_message",
    source=transactions_topic,
    message_type=TransactionMessage,
    output_features={
        Transaction.id: F.cast(_.id, pa.int64()),
        Transaction.amount: _.amount,
        Transaction.at: _.at,
        Transaction.memo: _.memo,
    },
)
