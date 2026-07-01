"""PyTorch regressor — `@model_handler` predict path.

The model is TorchScript-serialized at registration (`torch.jit.script`), so the
module must be scriptable; a plain `nn.Sequential(nn.Linear(...))` is. Inside the
container `self.model` is the loaded ScriptModule.
"""

from __future__ import annotations
from importlib.metadata import files

import numpy as np
import pandas as pd
import pyarrow as pa
import torch
import torch.nn as nn

from chalk.client import ChalkClient
from chalk.ml import model_handler

FEATURES = ["f0", "f1", "f2", "f3"]

MODEL_NAME = "stab_pytorch_reg"
# A dict input_schema registers four scalar float inputs (tabular) rather than a
# single tensor — tensor-input models aren't callable from the Run tab.
INPUT_SCHEMA = {f: pa.float64() for f in FEATURES}
OUTPUT_SCHEMA = {"prediction": pa.float64()}
# The scaling-group image only bakes in deps declared here (plus chalkpy). List
# everything `predict` imports so the container matches local.
DEPENDENCIES = ["torch", "numpy", "pandas", "pyarrow", "chalkdf"]
SAMPLE_ROWS = {
    "f0": [0.5, -1.0],
    "f1": [1.5, 0.2],
    "f2": [-0.5, 0.8],
    "f3": [0.1, -0.3],
}


@model_handler
class TorchReg:
    def load_model(self):
        self.default_load_model()  # restores the ScriptModule into self.model
        self.model.eval()

    def predict(self, df) -> pd.DataFrame:
        # Select FEATURES by name so column order matches training regardless of
        # how the inputs arrive.
        X = df.to_pandas()[FEATURES].to_numpy().astype(np.float32)
        with torch.no_grad():
            out = self.model(torch.from_numpy(X)).numpy().ravel()
        return pd.DataFrame({"prediction": out})


def build(version: int) -> TorchReg:
    torch.manual_seed(version)
    model = nn.Sequential(nn.Linear(len(FEATURES), 8), nn.ReLU(), nn.Linear(8, 1))
    model.eval()
    return TorchReg(model=model, files))


if __name__ == "__main__":
    client = ChalkClient()
    v = client.register_model_version(
        name=MODEL_NAME,
        model=build(version=1),
        input_schema=INPUT_SCHEMA,
        output_schema=OUTPUT_SCHEMA,
        dependencies=DEPENDENCIES,
    )
    # Registry name keeps underscores; scaling-group name must be hyphenated.
    # Matches the deploy-harness convention: "<hyphenated-name>-v<version>".
    client.deploy_model_version_to_scaling_group(
        name=f"{MODEL_NAME.replace('_', '-')}-v{v.model_version}",
        model_name=MODEL_NAME,
        model_version=v.model_version,
    )
