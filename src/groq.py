from chalk.features import before_all
import os
from chalk import chalk_logger

GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", None)
GROQ_MODEL_PROVIDER: str = "openai"
GROQ_MODEL: str = "llama3-8b-8192"
GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
