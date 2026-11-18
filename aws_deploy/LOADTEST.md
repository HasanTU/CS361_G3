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

Script: [`locustfile.py`](locustfile.py). Locust must be installed (`locust --version`).

Use the **API URL**, not the website URL. `monitoring.py check` and `deploy_v2.py`
both print it:

```bash
locust -f aws_deploy/locustfile.py --host https://<api-id>.execute-api.us-east-1.amazonaws.com
```

Open **http://localhost:8089**, enter the **number of users** and the **ramp up**
(users started per second), then press **Start**.

> **Class ground rules:** your own API URL only; test accounts only; about 80% reads and
> 20% writes; no uploads, no deletes, no paid APIs; **max 80 users, 2 minutes per step**;
> stop as soon as you cross your line. The locustfile already follows these rules.

Step up and watch the CloudWatch dashboard at each step:

| Step | Users | Ramp up | Run for |
|---|---|---|---|
| 1. warm-up | 10 | 2 | 2 min |
| 2 | 20 | 4 | 2 min |
| 3 | 40 | 5 | 2 min |
| 4 | 80 (max) | 10 | 2 min, or stop earlier if errors start or latency jumps |

What each simulated user does:
- Logs in once with a load-test account. Admins (`AdminUser`, 1 in 4) use 01–02; users
  (`NormalUser`, 3 in 4) use 03–08.
- Waits 1–3 s between actions, like a real person.
- Picks an action. About 40% list, 40% open a document, 20% write. Admins write by
  editing a `[LOADTEST]` document's sender and date; users write by moving one of
  their documents to its next status. Nothing is uploaded or deleted.

In the UI, **Statistics** shows each endpoint's p50/p95 and failures, and **Charts**
shows requests/s and response time as users grow. The point where response time shoots
up or failures start is your capacity limit. Download the CSVs from the
**Download Data** tab for the report.

Without the UI (for example, to save results for each round):

```bash
locust -f aws_deploy/locustfile.py --host https://<api-id>.execute-api.us-east-1.amazonaws.com --headless -u 40 -r 5 -t 2m --csv results/step3
```

Everything Locust creates belongs to the test accounts, so Step 4 removes it.

These are the endpoints the locustfile calls:

| Action | Type | Endpoint |
|---|---|---|
| Log in (once per simulated user) | n/a | `POST /api/v1/auth/login` with JSON `{"email", "password"}`. The response has `token` |
| View document list | list page | `GET /api/v1/documents` |
| Open a document | detail page | `GET /api/v1/documents/<id>` |
| Edit a document (admin) | write | `PATCH /api/v1/documents/<id>` with JSON `{"sender", "document_date"}`, on `[LOADTEST]` documents only |
| Update status (user) | write | `PATCH /api/v1/documents/<id>/status` with JSON `{"status": "ACKNOWLEDGED"}` |

Send the token as `Authorization: Bearer <token>`. `POST /api/v1/documents` (register
with a file upload) is a real user action too, but the class rules forbid uploads, so
the load test doesn't call it.

> **Likely bottleneck:** for admins, `GET /api/v1/documents` loads the **whole**
> collection with no pagination (`find_all()`). It gets slower as rows grow. Watch the
> Lambda duration and MongoDB in CloudWatch while the test runs.

## Monitoring with CloudWatch

Script: [`monitoring.py`](monitoring.py)

**Before class**, check that CloudWatch can see our resources. This only reads:

```bash
uv run aws_deploy/monitoring.py check
```

It checks:
- CloudWatch access.
- Each of the 3 Lambdas (`documents`, `search`, `auth`): memory, timeout, the last 24h of
  invocations, errors and throttles, and whether its logs exist.
- The API Gateway URL.
- The **Lambda account concurrency limit**: how many Lambda containers can run at once,
  shared by all 3 functions. Our account showed **400**.

It ends with `CloudWatch is usable` or lists what's missing. A `WARN` about empty logs
only means that function hasn't been called yet. Open the website once and run it again.

Then create the dashboard. Run it again any time to update it:

```bash
uv run aws_deploy/monitoring.py dashboard
```

It prints the link to the `cs361-g3-loadtest` dashboard. **While Locust runs**, set the
time range to **15m** and auto-refresh to **10s**. The dashboard shows:

| Row | Widgets | What to look for |
|---|---|---|
| API Gateway | requests/min, latency p50/p95/p99, 4xx/5xx | p95 climbing as users increase means something is saturating |
| Lambda | invocations, duration p95 and max per function | which function is slow: `documents` list is the usual suspect |
| Lambda scaling | concurrent executions, throttles, errors | **Throttles > 0** means we hit the concurrency cap, not slow code |
| Logs | cold starts, init time, p95, memory per minute | many cold starts means new containers spinning up; memory near 256 MB means increase it |

How to read the results:
- **Latency rises, no errors:** slow code or a slow database. Compare Lambda duration
  with MongoDB Atlas metrics.
- **Throttles or 429/503 errors:** Lambda concurrency limit. Scaling means more
  concurrency, not more memory.
- **5xx errors with duration near the timeout (15–20 s):** the database connection is
  struggling. Check Atlas **Connections**.

> **Likely scaling limit: MongoDB, not Lambda.** Each Lambda container opens one MongoDB
> connection (`maxPoolSize=1`). With up to 400 containers, Atlas M0's limit of about 500
> connections is close. Watch Atlas **Connections** as the user count goes up.

**Not in CloudWatch:**
- MongoDB Atlas: open the Atlas UI, then your cluster, then **Metrics** (connections,
  operations, query time).
- AWS credits left: the Learner Lab page (top bar). There's no API for this.

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
