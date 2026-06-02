#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=1.5.13"]
# ///
import chalkcompute

container = chalkcompute.Container(
    image=(
        chalkcompute.Image.base("vllm/vllm-openai:latest")
        .env({
            "HF_HOME": "/root/.cache/huggingface",
            "VLLM_ATTENTION_BACKEND": "FLASHINFER",
        })
    ),
    name="qwen-vllm-server",
    gpu="nvidia-l4:1",
    cpu="4",
    memory="16Gi",
    port=8000,
    lifetime="28800s",  # 8 hours; rerun this script if the server expires
    secrets=[chalkcompute.Secret.from_local_env("HF_TOKEN")],
    entrypoint=[
        "python3", "-m", "vllm.entrypoints.openai.api_server",
        "--model", "Qwen/Qwen2.5-7B-Instruct",
        "--port", "8000",
        "--trust-remote-code",
        "--max-model-len", "4096",
        "--dtype", "bfloat16",
        "--gpu-memory-utilization", "0.90",
    ],
)


if __name__ == "__main__":
    container.run()
