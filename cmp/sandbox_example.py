#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12,<3.14"
# dependencies = ["chalkcompute>=2.1.1"]
# ///
from chalkcompute import Sandbox, Image, Volume

if __name__ == "__main__":
    # Volume("code").put_file("hello.py", "print('hello world!')").put_file(...)
    sandbox = Sandbox(
        cpu="2",
        memory="4Gi",
        image=(
            Image.debian_slim(python_version="3.13").run_commands("pip install claude")
        ),
        # volumes=[("code", "/code")],
    ).run()
    print(sandbox.exec("echo", "hello!").stdout_text)
    sandbox.terminate()
