import os

# Fake credentials and no LLM, so a test that forgets a mock or fake fails instead of reaching a real AWS account or
# model; these win over backend/.env.
os.environ.update(AWS_ACCESS_KEY_ID="testing", AWS_SECRET_ACCESS_KEY="testing", AWS_SESSION_TOKEN="testing",
                  LLM_ENABLED="false", LLM_PROVIDER="bedrock", GEMINI_API_KEY="", GROQ_API_KEY="")
