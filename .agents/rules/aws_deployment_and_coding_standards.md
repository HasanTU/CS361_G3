---
trigger: always_on
description: Coding constraints, logging rules, and AWS Learner Lab deployment standards.
---

# Project Guidelines & AWS Serverless Standards

## 1. Codebase Integrity & Non-Invasive Extensions
- **Never modify teammate files**: Existing source files in `backend/`, `frontend/`, `docker-compose.yml`, `Dockerfile`, and test suites must remain 100% untouched.
- **Adapter Pattern**: All new infrastructure, storage, or cloud integrations must be implemented in standalone modules (e.g. `aws_deploy/`) and injected dynamically at runtime.

## 2. Tooling & Logging Standards
- **Python Tooling**: Always use `uv` for package management, virtual environments, and script execution (`uv run`).
- **No Emojis in Logging**: Strictly avoid emojis in CLI output, deployment logs, and script terminal displays. Use standard, professional text tags: `[INFO]`, `[Done]`, `[ERROR]`, `info:`, `error:`.
- **Clean Repository**: Do not leave temporary build artifacts, `.zip` files, or scratch markdown files in the git working tree.

## 3. AWS Learner Lab & Serverless Invariants
- **API Gateway for Public Access**: AWS Learner Lab enforces Service Control Policies (SCPs) that block unauthenticated Lambda Function URLs (`HTTP 403 Forbidden`). Always use **AWS API Gateway HTTP API (`apigatewayv2`)** with `$default` catch-all route and `AWS_PROXY` payload 2.0.
- **Lightweight Packages (Exclude boto3)**: `boto3` and `botocore` are pre-installed in AWS Lambda Python runtimes. Never bundle them into deployment packages; keep package sizes under ~12 MB.
- **S3-Backed Deployment**: Always upload `.zip` packages to S3 first (`s3_client.upload_file`) and deploy to Lambda via `S3Bucket`/`S3Key` to prevent direct upload connection timeouts.
- **Reserved Environment Variables**: Never pass `AWS_REGION` or `AWS_DEFAULT_REGION` inside Lambda's `Environment={"Variables": ...}` configuration, as AWS rejects reserved keys with `InvalidParameterValueException`.

## 4. MongoDB Atlas Connection Normalization
- **URI Path Validation**: Ensure MongoDB Atlas connection strings always include the default database name (e.g. `/e_mailbox`) before query parameters (`?appName=...`). Automatically normalize URIs if the database name is omitted to prevent PyMongo `ConfigurationError`.
