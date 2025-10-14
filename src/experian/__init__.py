import json
from datetime import date, datetime, timedelta

from chalk import DataFrame
from src.models import User


class ExperianClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_credit_report(
        self,
        name: str,
        dob: date,
    ):
        return DataFrame(
            {
                User.credit_report.id: [123],
                User.credit_report.raw: [
                    json.dumps(
                        {
                            "Tradelines": [
                                {
                                    "Id": 1,
                                    "OpenedAt": (datetime.now() - timedelta(days=80)).isoformat(),
                                    "ClosedAt": None,
                                    "Kind": "card",
                                    "Balance": 7203.40,
                                    "Amount": 10000.0,
                                    "AmountPastDue": 0.0,
                                    "PaymentAmount": 200.0,
                                },
                                {
                                    "Id": 2,
                                    "OpenedAt": (datetime.now() - timedelta(days=8)).isoformat(),
                                    "ClosedAt": None,
                                    "Kind": "auto",
                                    "Balance": 7203.40,
                                    "Amount": 10000.0,
                                    "AmountPastDue": 0.0,
                                    "PaymentAmount": 200.0,
                                },
                            ],
                        }
                    )
                ],
            }
        )
