# Load-Test Data Guide

How to fill the deployed e-Mailbox with **fake** accounts and documents before the
load-test class, and how to remove all of it afterwards.

Script: [`loadtest_data.py`](loadtest_data.py)

---

## Why we need this

The class sends thousands of requests per round, about 20% of them writes. An almost
empty database answers every request instantly, so it would show no bottleneck at all.
The course asks for:

- **5–10 test accounts**: we create 8.
- **Hundreds of rows in the main table**: our main table is the MongoDB `documents`
  collection. We add 500 by default.
- **No real user data, plus a way to delete it later**: everything is fake and tagged,
  and `cleanup` removes it.

---

## What the script creates

Our data lives in three places, so the script writes to all three:

| Where | What | How it's marked |
|---|---|---|
| **Cognito** (users) | `loadtest01@example.com` … `loadtest08@example.com`. 01–02 are `ADMIN`, 03–08 are `USER` | the `loadtest` email prefix |
| **MongoDB Atlas** `documents` | 500 fake documents: Thai titles, dates over the past year, mixed statuses, each assigned to one `USER` test account | `loadtest: true` field and `[LOADTEST]` in the title |
| **S3** document bucket | one tiny real PDF (~600 B) per document, so preview/download do real work | key starts with `uploads/loadtest_` |

All test accounts use the same password, `LoadTest123!`. It's a test-only value, and
you can override it with the `LOADTEST_PASSWORD` environment variable.

---

## Before you run it

1. **Start the AWS Learner Lab** and copy fresh credentials into `.env` (see
   [README.md → Prerequisites](README.md#prerequisites)). Lab credentials expire every
   session, so update them each time.
2. Check that `.env` contains:
   - `MONGO_URI`: the **Atlas** connection string, not `localhost`
   - `COGNITO_USER_POOL_ID`: written there by `setup_cognito.py`
3. The system should already be deployed (`deploy_v2.py`), so the S3 bucket
   `cs361-g3-documents-<ACCOUNT_ID>` exists.
4. Install [`uv`](https://docs.astral.sh/uv/). It installs `boto3` and `pymongo` for the
   script automatically.

Run every command from the **project root** (`CS361_G3/`).

---

## Step 1: Seed the data

```bash
uv run aws_deploy/loadtest_data.py seed
```

What happens:

1. Checks your AWS credentials (prints account and region).
2. Creates or refreshes the 8 Cognito accounts, sets their password, and adds each one
   to its `ADMIN` or `USER` group.
3. Builds 500 documents and uploads one PDF per document to S3 (16 uploads in parallel).
4. Inserts the documents into MongoDB in one batch.
5. Prints how many test documents exist and the collection's total size.

Want more rows? Run it again or pass a number. It **adds** documents and doesn't
duplicate the users:

```bash
uv run aws_deploy/loadtest_data.py seed --documents 1000
```

## Step 2: Check it worked

```bash
uv run aws_deploy/loadtest_data.py status
```

This only reads data. It shows how many test accounts exist, how many seeded documents
there are, and how many documents `cleanup` would delete.

You can also log in on the website as `loadtest01@example.com` (admin, sees all
documents) or `loadtest03@example.com` (user, sees only the documents assigned to them).

## Step 3: Run the load test (in class)

Use the test accounts in Locust. These are the API calls our users make most often:

| Action | Type | Endpoint |
|---|---|---|
| Log in (once per simulated user) | n/a | `POST /api/v1/auth/login` with JSON `{"email", "password"}`. The response has `token` |
| View document list | list page | `GET /api/v1/documents` |
| Open a document | detail page | `GET /api/v1/documents/<id>` |
| Register a document (admin) | write | `POST /api/v1/documents` (multipart: `title`, `document_date`, `sender`, `receiver`, `file`) |
| Update status (user) | write | `PATCH /api/v1/documents/<id>/status` with JSON `{"status": "ACKNOWLEDGED"}` |

Send the token as `Authorization: Bearer <token>`. For `receiver`, use a test account's
name (for example `LoadTest User 03`) so new documents stay inside the test data.

> **Likely bottleneck:** for admins, `GET /api/v1/documents` loads the **whole**
> collection with no pagination (`find_all()`). It gets slower as rows grow. Watch the
> Lambda duration and MongoDB in CloudWatch while the test runs.

## Step 4: Clean up (after class)

```bash
uv run aws_deploy/loadtest_data.py cleanup
```

The script lists what it will delete, then asks you to type `delete`. It removes:

1. Every document tagged `loadtest: true`.
2. Every document **created during the load test**: uploaded by a test account, or
   assigned only to test accounts. Locust's writes have no tag, so this step catches them.
3. The S3 file of each deleted document.
4. The 8 Cognito test accounts.

A real document is **never** deleted just because a test account was added alongside
real recipients. The script lists those documents and keeps them.

Use `--yes` to skip the prompt, for example in a script. Run `status` afterwards to confirm
everything shows 0.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `AWS Authentication Failed` | The lab session expired. Copy new credentials into `.env` |
| `COGNITO_USER_POOL_ID is not set` | Run `uv run aws_deploy/setup_cognito.py` first |
| Prompt asks for a MongoDB URI | `MONGO_URI` in `.env` is missing or points to `localhost`. Use the Atlas string |
| MongoDB timeout | In Atlas **Network Access**, allow your current IP (or `0.0.0.0/0`) |
| `AccessDenied` on S3 | The bucket only allows `LabRole`. Use the Learner Lab credentials, not personal ones |
