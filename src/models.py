import os

# from openai import OpenAI

from chalk import DataFrame, FeatureTime, Windowed, windowed, _, feature
from chalk.features import features


@features
class Transaction:
    id: int
    amount: float
    memo: str
    user_id: "User.id"
    user: "User"
    at: FeatureTime
    category: str = "Unknown"
    completion: str = feature(max_staleness='infinity')
    is_fraud: bool = _.category == "Food"


@features
class User:
    id: int
    # name: str
    transactions: DataFrame[Transaction]
    count_transactions: Windowed[int] = windowed(
        "1d", "30d", "90d",
        expression=_.transactions[
            _.amount,
            _.at > _.chalk_window,
            _.category == "Food",
        ].count(),
    )
    amount_transactions: Windowed[int] = windowed(
        "1d", "30d",
        expression=_.transactions[_.amount, _.at > _.chalk_window].sum(),
    )


# os.environ["OPENAI_API_KEY"] = "sk-svcacct-goVE0hlYSZqGVgfjuaxUoGnr_Q8H7rA094gFPNd_YNSVVT3BlbkFJzwbndO_o6M8odF_aLJ9DWxeqsTsD-34NThgNW4he1VFAA"
# # os.environ["OPENAI_API_KEY"] = "sk-RfHL__rHGfs-KmeQgJj5iL0LAMY3ZwBj4KKJIPIafqT3BlbkFJ_VXU7JXYeW4QUnn0Jr1X_5ILbrXtXeIzMvjSdh4UwA"
#
# "AIzaSyCEgFSw5mRj-POYuvhJJKhIfw76NJxaUo0"
#
# "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"
#
# client = OpenAI()
#
# completion = client.chat.completions.create(
#     model="gpt-4o-mini",
#     messages=[
#         {"role": "system", "content": "You are a helpful assistant."},
#         {
#             "role": "user",
#             "content": "Write a haiku about recursion in programming."
#         }
#     ]
# )
#
# print(completion.choices[0].message)
#
