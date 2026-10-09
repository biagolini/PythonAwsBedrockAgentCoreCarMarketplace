# AgentCore Runtime requires linux/arm64 images serving HTTP on port 8080 with
# /invocations and /ping routes. BedrockAgentCoreApp provides both.
# The platform is set at build time: docker buildx build --platform linux/arm64
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py .

EXPOSE 8080

# ADOT auto instrumentation exports model and tool calls as OpenTelemetry spans,
# which feed the CloudWatch GenAI Observability views used in Demo 3.
CMD ["opentelemetry-instrument", "python", "agent.py"]
