import os

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", None)
GROQ_MODEL_PROVIDER: str = "openai"
GROQ_MODEL: str = "openai/gpt-oss-20b"
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
