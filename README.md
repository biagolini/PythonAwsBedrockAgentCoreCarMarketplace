# Amazon Bedrock AgentCore: Car Marketplace Assistant

**Author:** Carlos Biagolini-Jr.

**LinkedIn:** https://www.linkedin.com/in/biagolini/

**Medium:** https://medium.com/@biagolini

> **Didactic notice:** This project was developed as a practical example to accompany a blog post. The architecture decisions made here were chosen to meet an educational objective. They illustrate concepts clearly, not necessarily in the most production-ready way. Feel free to draw inspiration from these ideas, but remember to evaluate the limitations and constraints of your own business before adopting any of them. This project is released under the [MIT License](./LICENSE): you are free to copy, modify, and use it as you wish, but it comes with no warranties and the author takes no responsibility for its use in any environment.

## Overview

A fictional online used car marketplace runs one AI sales assistant per partner dealership. This repository holds everything needed to run that assistant on Amazon Bedrock AgentCore, with the inventory stored in MongoDB Atlas:

1. One agent container for AgentCore Runtime, built and pushed to Amazon ECR by GitHub Actions.
2. A Lambda tool that searches the inventory in MongoDB Atlas, exposed to the agent through AgentCore Gateway, with its deployment zip already built.
3. Fictional data to load into Atlas.

All names, prices, and phone numbers in this repository are fictional.

## Repository layout

| Path | What it is |
|---|---|
| `agent/` | The agent: a LangGraph agent on an Amazon Bedrock model. If `GATEWAY_URL` is set and reachable, it loads the tools from the AgentCore Gateway; otherwise it answers without tools instead of breaking (graceful fallback). One image covers both the no-tools and the with-tools demo. |
| `lambda/search_cars/` | Lambda tool: available cars by body type, price range, and make, read from MongoDB Atlas with AWS IAM authentication (no password). `tool_schema.json` is the Gateway inline schema. |
| `lambda/dist/search_cars.zip` | Prebuilt deployment package for the Lambda (arm64, Python 3.14, `pymongo[aws]` bundled). Rebuild with `./lambda/build.sh`. |
| `data/` | `stores.json` and `cars.json`, plus `seed_mongodb.py` to load them into the `car_marketplace` database. |
| `scripts/` | `build_and_push.sh` (build and push the image from your machine) and `invoke_runtime.py` (chat with a deployed runtime from the terminal). |
| `.github/workflows/deploy-agents.yml` | Builds the image for `linux/arm64` and pushes it to ECR on every push to `main` that touches `agent/`. |

## Container image

| ECR image | Environment variables |
|---|---|
| `agentcore/dealership-assistant-agent:latest` | optional `BEDROCK_MODEL_ID`, optional `GATEWAY_URL` |

One image, tagged `latest`. `BEDROCK_MODEL_ID` is optional: the agent defaults to `us.amazon.nova-2-lite-v1:0`, so a runtime with no variables set still starts. `GATEWAY_URL` is optional too: set it to give the agent the inventory tool through the Gateway; leave it unset and the agent runs with no tools.

### Pipeline setup (once)

The workflow authenticates with GitHub OIDC, with no long lived keys:

1. In IAM, GitHub is registered as an OpenID Connect identity provider (`token.actions.githubusercontent.com`, audience `sts.amazonaws.com`).
2. A role trusted by that provider for this repository, allowed to push to the ECR repository and to create it on the first run.
3. Two repository secrets (**Settings > Secrets and variables > Actions**): `AWS_ROLE_ARN` with the role ARN, and `AWS_REGION` (for example `us-east-1`).

Every push to `main` that changes `agent/` builds the image for `linux/arm64` and pushes it tagged `latest`. It can also be started by hand from **Actions > Deploy agent to ECR > Run workflow**.

To push from your machine instead:

```bash
AWS_PROFILE=<your-profile> ./scripts/build_and_push.sh
```

## Run the agent locally

```bash
cd agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
AWS_PROFILE=<your-profile> BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0 python agent.py
```

```bash
curl -s localhost:8080/invocations -H 'Content-Type: application/json' \
  -d '{"prompt": "Hello, how are you?"}'
```

## Deploy on AgentCore Runtime

1. **Amazon Bedrock AgentCore > Runtime > Create runtime**, platform V1 (ready in seconds, good for quick iterations; V2 for production, the agent is snapshot safe), microVMs.
2. **Agent source:** ECR container, `agentcore/dealership-assistant-agent:latest`.
3. **Permissions:** let the console create the execution role.
4. **Inbound auth:** IAM (SigV4), the default.
5. **Environment variables:** none required to start; set `GATEWAY_URL` to give the agent the inventory tool, and `BEDROCK_MODEL_ID` only to override the default model.

Test it from the console with `{"prompt": "Hello, how are you?"}`, or from the terminal:

```bash
AWS_PROFILE=<your-profile> python scripts/invoke_runtime.py --arn <runtime-arn>
```

To give a running runtime the inventory tool, add the `GATEWAY_URL` environment variable (the Gateway resource URL) and grant its execution role `bedrock-agentcore:InvokeGateway` on that Gateway. No image change is needed: the same image runs with or without tools.

## Inventory search tool (`search_cars`)

1. **Data:** put a connection string with write access in a local `.env` file (`MONGODB_URI=...`, never committed) and run `python data/seed_mongodb.py`. It drops and reloads only the `stores` and `cars` collections.
2. **IAM role:** `atlas-demo-role`, trusted only by the Lambda function `dealership-search-cars`, with `AWSLambdaBasicExecutionRole` attached.
3. **Atlas user:** AWS IAM authentication, type IAM Role, the ARN of `atlas-demo-role`, privilege **read** on `car_marketplace` only.
4. **Lambda:** `dealership-search-cars`, Python 3.14, arm64, role `atlas-demo-role`, code from `lambda/dist/search_cars.zip`, timeout 15 seconds, and:

   ```
   MONGODB_URI = mongodb+srv://<your-cluster-host>/?authSource=%24external&authMechanism=MONGODB-AWS
   ```

5. **Test event:** `{"body_type": "SUV", "min_price": 20000, "max_price": 25000}`.
6. **Gateway target:** type Lambda ARN, outbound auth with the Gateway IAM role (allowed to `lambda:InvokeFunction` on this function), inline schema from `lambda/search_cars/tool_schema.json`.

Design choices: one fixed query shape, read only database user, results capped at 20, text filters escaped, and unknown arguments dropped (AgentCore Gateway forwards arguments even when they are not declared in the tool schema).

## Observability

Both containers start under `opentelemetry-instrument`, so model and tool calls are exported as OpenTelemetry spans. Enable CloudWatch Transaction Search once per account and Region, then open **CloudWatch > GenAI Observability**.

## Cleanup

Delete the runtimes, the ECR repository, the Gateway and its targets, the Lambda function, the IAM roles (`atlas-demo-role`, the Gateway role, the runtime execution roles, the GitHub OIDC role), and the Atlas database user mapped to `atlas-demo-role`.
