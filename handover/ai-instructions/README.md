# AI handover: backend_v2 auth bypass fix

Snapshot: 2026-10-31. Read this whole file before you change anything. Section 1 is the rules. Section 7 is the exact next steps.

## 0. Quick facts

| Item | Value |
|---|---|
| Repo root | `CS361_G3/` (this git repository). Every path below is relative to it. Run every command from it. |
| Base commit | `b52f483` (`origin/main` when the work started) |
| Fix commit | `4bda181` fix(backend-v2): verify Cognito tokens and authorize documents by sub (40 files) |
| Other commits | `fbbc630` removed `Reviews-fromAI` (agent notes). The commit after it added this `handover/` folder. |
| Pushed | Not at the time of writing. Check with `git --no-pager log --oneline origin/main..HEAD`. |
| Deployed | No. AWS (Academy lab) and MongoDB were offline. A deployed API, if any, still runs the vulnerable code. |
| Stored data migrated | No |
| Tests | 379, all offline, in `tests/v2/`. The last full run passed. |
| Spec progress | 20 of 28 tracked items done (tasks 1, 2 and 3.1 to 3.18) |

### The spec copy

`spec/` holds the spec as it was at handover:
- `bugfix.md`: current behavior (1.x), expected behavior (2.x), unchanged behavior (3.x).
- `design.md`: decisions, signatures, messages, test plan, post-deploy smoke check.
- `tasks.md`: the task list. `[x]` done, `[-]` in progress, `[~]` queued, `[ ]` not started.
- `exploration-results.md`: test evidence before and after the fix.

From now on, treat `spec/` as the working copy: tick checkboxes in `spec/tasks.md` and append results to `spec/exploration-results.md`.
- Absolute paths in the spec (`/home/rumi/Works/antigravity/universityClasses/cs361/CS361_G3/...`) mean this repo on the owner's machine. Translate them to your checkout.
- Paths starting with `.kiro/specs/backend-v2-auth-bypass-fix/` mean `handover/ai-instructions/spec/`.
- To run the tasks from Kiro, copy `spec/` to `.kiro/specs/backend-v2-auth-bypass-fix/` in your workspace (it is a bugfix spec), and copy updated files back here before committing.
- The security review the spec cites (`project-requirements/security-review-backend-v2.md`) is not in the repo. Ask the owner if you need it.

## 1. Rules

### 1.1 Always
- Run tests with `uv run --with-requirements requirements-dev.txt pytest -q`. Append a path to run one file. If `uv` cannot install `requirements-dev.txt`, stop and report. Do not fall back to a global `pip install`.
- Use `spec/design.md` as the source of truth for signatures, messages and status codes. If this file, `spec/tasks.md` and `spec/design.md` disagree, stop and report.
- Keep each change inside the files the task names.

### 1.2 Never
- Re-add a login fallback: no MongoDB login, no local password check, no dev admin, no locally minted tokens. Failing closed with 503 is intentional.
- Authorize by name, email, username or alias. Authorization uses only the verified Cognito `sub`. Names are only turned into a `sub` when a recipient is written.
- Decide admin from anything other than the exact group `"ADMIN"` in the verified `cognito:groups` (`Caller.is_admin`).
- Edit `tests/v2/test_bug_condition_exploration.py` or `tests/v2/test_preservation.py` to make them pass. They encode the bug and the behavior that must not change.
- Point tests at real Cognito, MongoDB or S3.
- Log tokens, claims, emails or passwords, or return exception text to clients.
- Edit these for this spec: `frontend/`, `backend/` (v1), `aws_deploy/helpers/*`, `aws_deploy/setup_cognito.py`, `aws_deploy/deploy.py`, `aws_deploy/destroy.py`, `aws_deploy/lambda_adapter.py`, `aws_deploy/lambda_handler.py`, `requirements.txt`, `Dockerfile`, `.gitignore`, `.env`, `.env.example`, `scripts/seed_mock_*.py`.

### 1.3 Ask the human first (an explicit yes for each)
- Deploying: `uv run aws_deploy/deploy_v2.py`.
- `scripts/migrate_assigned_to_sub.py --apply` and `--restore`.
- `git push`, and any commit the human did not ask for.
- Running `aws_deploy/setup_cognito.py`. It may create a new user pool, which changes every `sub`.
- Any IAM, Cognito or S3 configuration change in AWS.
- Changing the task order (see 7.4).

### 1.4 Stop and report when
- A test that should pass fails, or one that should fail passes.
- The code does not match what a task describes (a function, file or constant is missing or renamed).
- A command fails for an outside reason (network, credentials, missing tool).
- AWS returns `AccessDeniedException` for `cognito-idp:ListUsers` (decision D4). Do not add a fallback or change IAM.
- You would need to change a file the task does not list.

### 1.5 Git
- Run git only inside this repo. Stage files by name (`git add path/one path/two`), never `git add -A` or `git add .`.
- Never stage `.env`, `lambda_v2_package.zip`, `.lambda_v2_build/`, `.hypothesis/`, `.venv/` or `__pycache__/`.
- Never force-push, reset or rewrite history.
- Use `git --no-pager` for `log` and `diff` so commands do not hang in a pager.

## 2. What the owner asked for

Original bug report, in the owner's words:

> The main bug is that access control in backend_v2 can be bypassed. All three Lambdas (auth, documents, search) read the login token's contents without ever checking its signature, and there is no API Gateway authorizer in front of them. Anyone who can build a token can claim to be an admin or another user, which makes the file-access check useless even though the code calls it. Several things make it worse. Admin is guessed from text, so any identity containing "admin" or "staff" counts, including a display name the user sets. The server mints alg: none tokens itself and accepts them. A hard-coded dev admin login and a plaintext password compare act as fallbacks when Cognito errors. GET /auth/users needs no login. Documents are matched by display name and alias instead of the Cognito sub. The fix is to verify tokens with a JWT authorizer or Cognito's keys, take roles only from verified Cognito groups through one shared helper, authorize by sub, and remove the backdoors and fake-token code. You also listed two related problems. There is no system-generated document number, only a Mongo ObjectId. And forwarding overwrites the recipient list, so each document can go to only one professor.

Decisions made with the owner, in order:
1. Bugfix spec. Scope: findings 1 to 7 and 15 of the security review. The document number and multi-recipient forwarding go to separate specs (see `backlog.md`).
2. Tasks written so a weaker model can execute them without guessing.
3. Design approved as written: verification in code (not an API Gateway authorizer); after a forward the original receiver loses access; Cognito `ListUsers` for name resolution, with a stop-and-report if `LabRole` lacks the permission.
4. Agent files moved out of the repo before implementation.
5. Task 3.14: the integration flow was reordered to forward first, then acknowledge, because the state machine has no ACKNOWLEDGED to FORWARDED transition. `spec/design.md` was updated to match.
6. Task 3.17: `backend_v2/README.md` had no `/api/v1/auth/users` row; the owner chose to add one.
7. One detailed fix commit, one small chore commit, and this handover folder inside the repo (explicit request).

## 3. Analysis: root causes

Confirmed by reading the code at `b52f483`, then by running the exploration test (12 failures, evidence in `spec/exploration-results.md`):

1. **No token verification anywhere.** `_decode_jwt_payload` was copied into the three handlers and `auth/use_cases.py`. It base64-decoded the payload. `configure_apigw_v2` in `aws_deploy/deploy_v2.py` creates routes with no authorizer.
2. **The server minted unsigned tokens.** `AuthUseCases._synthesize_test_token` built `alg: none` tokens (signature `mock_signature`). Cause 1 made them work everywhere.
3. **Fail-open login.** Any `initiate_auth` error whose text did not contain `NotAuthorizedException` or `UserNotFoundException` fell through to a MongoDB lookup that accepted `password_hash == password`, then to `admin@kanna.ac.th` / `admin1234`.
4. **Role decided by text, in three copies.** `_parse_auth` (documents, search) granted admin when any identity contained "admin" or "staff", matched a hard-coded list, or `custom:role`/`role` said admin. `login` used a different rule, so the login role and the enforced role could disagree.
5. **Authorization by mutable strings.** An identity list (email, name, username, sub, `KNOWN_USER_ALIASES` expansions) was matched case-insensitively against `assigned_to` and `receiver`.
6. **Unauthenticated directory.** `GET /api/v1/auth/users` never read a token.
7. **Leaky errors and logs.** Handlers returned `Internal server error: <exception>`; the documents handler printed `[AUTH_DEBUG]` identity lines.

## 4. Conclusion

One wrong assumption, trusting the token's contents, was repeated in four places and stacked with text-based roles and fallbacks. The fix replaces all of it with one verified identity: every Lambda verifies the Cognito ID token before any work, admin comes only from the verified `ADMIN` group through one shared helper, and documents are authorized only by the verified `sub`. All fallbacks and fake-token code are gone. The 12 exploration tests failed before the fix and pass after it; the 34 preservation tests pass before and after. The remaining risk is operational: deploying, the unverified `ListUsers` permission, and migrating stored data.

## 5. Design decisions (full text in `spec/design.md`, "Key Decisions")

- **D1** Verify in code with PyJWT against the user pool JWKS, not an API Gateway JWT authorizer. An authorizer can't read the `auth_token` cookie (requirement 3.5), replaces the 401 body (3.6), would force splitting the shared `ANY /api/v1/auth/{proxy+}` route because login and logout stay public, and can't be tested offline (3.17).
- **D2** `pyjwt[crypto]>=2.10.1,<3`. 2.10.0 had an issuer-check bug (CVE-2024-53861).
- **D3** Accept ID tokens only (`token_use == "id"`); they carry `aud`, `email`, `name`, `cognito:groups`.
- **D4** Resolve names with Cognito `ListUsers` behind `UserDirectoryPort` (`CognitoUserDirectory`, cached 300 s). Whether `LabRole` allows `cognito-idp:ListUsers` is not verified.
- **D5** The server turns recipient names into `sub` values; the frontend is unchanged. Residual risk: users can change their own Cognito `name`, which can make a name ambiguous (400). That is denial of service, not a leak.
- **D6** `build_v2_package()` in `deploy_v2.py` zips the whole `backend_v2/` tree for all three functions, so `backend_v2/shared/auth/` ships automatically. `aws_deploy/helpers/lambda_builder.py` is v1 only.
- **D7** Build wheels for `x86_64-manylinux2014` with `--only-binary :all:`. The `python3.11` runtime runs on Amazon Linux 2 (glibc 2.26); uv's `linux` target means manylinux_2_28, whose `cryptography` wheel won't import there.
- **D8** pytest and Hypothesis, offline, with an RSA key generated at test time.

## 6. Architecture after the fix

### 6.1 Deployment shape
- API Gateway HTTP API `cs361-g3-v2-api`, routes `ANY /api/v1/<auth|documents|search>` and `.../{proxy+}`, payload format 2.0.
- Lambdas `cs361-g3-v2-auth`, `cs361-g3-v2-documents`, `cs361-g3-v2-search`: Python 3.11, x86_64 (default), role `LabRole`, one shared zip.
- Environment: `MONGO_URI`, `S3_BUCKET_NAME`, `FRONTEND_ORIGIN`, `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`.
- Data: file bytes in S3 (`uploads/<uuid>_<sanitized name>`); document records in MongoDB collection `documents`; identities only in Cognito.

### 6.2 Module map
| Path | Role |
|---|---|
| `backend_v2/shared/auth/errors.py` | Messages; `AuthenticationError(PermissionError)` maps to 401, `AuthServiceUnavailableError` to 503 |
| `backend_v2/shared/auth/caller.py` | `Caller(sub, email, name, groups, faculty, claims)`, `is_admin`, `role`, `actor_label`; `caller_from_claims`; `require_admin` |
| `backend_v2/shared/auth/token_verifier.py` | `CognitoTokenVerifier.verify(token)`; `from_env()` |
| `backend_v2/shared/auth/request_auth.py` | `extract_token(event)`, `authenticate(event)`, `get_token_verifier()`, `set_token_verifier()` |
| `backend_v2/shared/domain/directory_user.py` | `DirectoryUser`, `resolve_recipient(s)` |
| `backend_v2/shared/ports/user_directory.py` | `UserDirectoryPort.list_users()` |
| `backend_v2/shared/adapters/cognito_user_directory.py` | `CognitoUserDirectory` (paginated `ListUsers`, 300 s cache, boto errors not caught) |
| `backend_v2/shared/domain/document.py` | `is_accessible_by(user_sub, is_admin)` |
| `backend_v2/shared/adapters/mongo_document_repository.py` | `find_by_assigned_user(user_sub)` is `{"assigned_to": sub}` |
| `backend_v2/functions/*/handler.py` | Routing and error mapping; `set_use_cases()` for tests |
| `backend_v2/functions/*/use_cases.py` | Business logic on ports |
| `scripts/migrate_assigned_to_sub.py` | One-time `assigned_to` migration (dry run by default) |

### 6.3 Rules the code enforces
- **Request order in every handler:** OPTIONS returns 204 before anything else; then `get_use_cases()` and `authenticate(event)` inside `try` (auth: login and logout don't authenticate); then routing.
- **Token lookup:** `Authorization: Bearer`, then `event["cookies"]` entry `auth_token=`, then the `Cookie` header. An empty `Bearer ` gives no token.
- **Verification:** RS256 only and `kid` required (checked before any network call); key from `<issuer>/.well-known/jwks.json` (cached 1 h, 5 s timeout); `iss` = `https://cognito-idp.<region>.amazonaws.com/<pool id>`; `aud` = `COGNITO_CLIENT_ID`; `exp`, `iat`, `sub`, `token_use` required, 10 s leeway; `token_use == "id"`.
- **Admin:** `"ADMIN" in cognito:groups`, exact and case-sensitive. Nothing else.
- **Access:** admins see everything; others only documents whose `assigned_to` contains their `sub`.
- **Recipients (register, assign):** exact `sub`, then email (casefold), then a display name matching exactly one directory user; otherwise 400. Duplicates are dropped by `sub`. Register resolves before saving the file and deletes the file if the database write fails.
- **Assign order:** 404 lookup, 403 permission, resolve, replace `assigned_to`, update. Status becomes ASSIGNED from RECEIVED, otherwise FORWARDED.
- **Audit fields:** `uploaded_by` and `status_history[].changed_by` hold `Caller.actor_label` (email, else `sub`).
- **Login:** Cognito `initiate_auth` only. Codes `NotAuthorizedException`, `UserNotFoundException`, `UserNotConfirmedException`, `PasswordResetRequiredException`, or a challenge response, give 401. Any other Cognito error, a missing client id, or a returned token that fails verification gives 503. The role comes from the verified token.

### 6.4 Status and message contract
- 401 `Authentication is required`: no token.
- 401 `Invalid authentication token`: forged, unsigned, expired, wrong `iss`/`aud`/`token_use`, unknown `kid`, malformed.
- 401 `Invalid email or password`: login with bad credentials.
- 503 `Authentication service is unavailable`: JWKS unreachable, or Cognito unusable at login.
- 403 `Admin privilege required to register documents` (checked before the content type).
- 403 `Access denied: You are not assigned to this document` (details, file).
- 403 `Access denied: You cannot update an unassigned document` (status).
- 403 `Permission required to assign or forward this document` (assign).
- 404 `Document was not found: <id>`; 404 `Route not found: <METHOD> <path>`.
- 400: `Email and password are required`, `recipients must be a list of strings`, `Must assign at least one recipient user ID`, `Recipient is required`, `Recipient must be a string`, `Unknown recipient: <value>`, `Recipient name is ambiguous: <value>`, `Content-Type must be multipart/form-data`, `Missing uploaded file`, `Missing required fields: <fields>`, `Invalid workflow transition: ...`, JSON parse errors.
- 500 `Internal server error`: anything else, including missing Cognito configuration. Details go to `logger.exception` only.
- Exception order: documents 401, 503, 403, 404, 400, 500. Search 401, 503, 403, 500. Auth 401 (`AuthenticationError`), 503, 401 (`PermissionError`), 400, 500.

### 6.5 Tests (`tests/v2/`, 379)
| File | Tests | Covers |
|---|---|---|
| `conftest.py` | - | Test RSA keys, token builders, static key provider, in-memory repo and storage, fake directory (9 seed users with fixed subs), fake Cognito, event builder, `app` and `auth_login` fixtures |
| `test_bug_condition_exploration.py` | 12 | Property 1: the bug cases (failed on `b52f483`) |
| `test_preservation.py` | 34 | Property 2: legitimate behavior unchanged (written from observed old behavior) |
| `test_role_consistency.py` | 51 | Property 3: one role rule in login, documents and `Caller` |
| `test_migration_plan.py` | 49 | Property 4 and migration script behavior |
| `test_documents_handler.py` | 76 | Documents status and message contract |
| `test_auth_handler.py` | 38 | Login, logout, users, me |
| `test_token_verifier.py` | 34 | Every verification failure mode |
| `test_directory_user.py` | 20 | Recipient resolution |
| `test_caller.py` | 17 | `Caller` and the admin rule |
| `test_integration_flows.py` | 12 | Flows across the three Lambdas |
| `test_search_handler.py` | 10 | Search Lambda |
| `test_document_access.py` | 8 | Sub-only access |
| `test_request_auth.py` | 7 | Token lookup order |
| `test_mongo_repository_query.py` | 6 | Exact `sub` query |
| `test_cognito_user_directory.py` | 5 | Pagination, filters, cache |

## 7. Task status and next steps

### 7.1 Status (task IDs from `spec/tasks.md`)
| ID | Task | Status | Evidence or gap |
|---|---|---|---|
| 1 | Bug condition exploration test | Done | 12 assertion failures on `b52f483`, recorded in `spec/exploration-results.md` |
| 2 | Preservation tests | Done | 34 passed on `b52f483`, three seeds |
| 3.1 to 3.7 | Shared auth package, directory, domain and repository changes | Done | Unit tests listed in 6.5 |
| 3.8 to 3.12 | Use cases and the three handlers | Done | Handler tests pass; exploration 12 of 12 pass after 3.12 |
| 3.13 | Role consistency property | Done | 51 passed |
| 3.14 | Integration flows | Done | Flow reordered with the owner's approval |
| 3.15 | Migration script and tests | Done | 49 passed; never run against a real database |
| 3.16 | `deploy_v2.py` changes | Done | Wheel resolve check passed; deploy not run |
| 3.17 | `backend_v2/README.md` | Done | Missing row added with the owner's approval |
| 3.18 | Re-run exploration | Done | 12 passed |
| 3.19 | Re-run preservation | **Partial** | Never run as its own step (dispatch cancelled twice). The file passed (34) inside every full-suite run after 3.12. |
| 3 | Parent of 3.x | Open | Closes when 3.19 is ticked |
| 4 | Checkpoint | **Partial** | The committed files matched the allowed list in task 4 exactly. The last full run (379 passed) came before the docs-only 3.17 edit. The final run, the full grep and the report were not done. |
| 5.1 | Deploy | Not started | Needs AWS and approval |
| 5.2 | Smoke check | Not started | Needs the deployed API |
| 5.3 | Migration | Not started | Needs MongoDB, Cognito and approval |
| 5.4 | Handover | Not started | Text in `spec/tasks.md` |

### 7.2 Procedure

**Step A, task 3.19 (offline).** Run each command separately; each must print `34 passed`:

    uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_preservation.py
    uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_preservation.py --hypothesis-seed=11
    uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_preservation.py --hypothesis-seed=12

On a failure, compare it with "Intentional differences that are not regressions" in `spec/design.md` and report before changing anything. Append the three summary lines to `spec/exploration-results.md` under "Preservation after the fix (task 3.19)" and tick 3.19 in `spec/tasks.md`.

**Step B, task 4 (offline).**

    uv run --with-requirements requirements-dev.txt pytest -q
    grep -rn --include='*.py' --include='*.md' -e _decode_jwt_payload -e _parse_auth -e KNOWN_USER_ALIASES -e AUTH_DEBUG -e mock_signature -e _synthesize_test_token -e admin1234 -e 'Internal server error: ' backend_v2/
    git status --short

Expect `379 passed`, no grep output, and no changes you didn't make. The `--include` filters skip stale `__pycache__/*.pyc` files that still contain the old names. A grep hit in a `.py` file is a bug; a hit in a `.md` file is stale documentation. Report either. Then tell the human the results and ask before starting task 5.

**Step C, task 5.1 deploy (approval required).**
- Prerequisites: the AWS Academy lab is running and boto3 can use its credentials; `.env` has `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` for the existing pool; MongoDB is reachable. The script exits if a Cognito variable is missing. Don't run `setup_cognito.py` to fix that without the human.
- Run `uv run aws_deploy/deploy_v2.py`. It may prompt for the MongoDB URI; let the human answer. Record the API endpoint it prints. If it fails, stop and report the output; don't retry with changed flags.

**Step D, task 5.2 smoke check.**
- Run the six checks in `spec/design.md`, "Integration Tests", "Post-deployment smoke check", with `curl` against the endpoint. Seed passwords are in `SEED_USERS` in `aws_deploy/setup_cognito.py`. Record status and body under "Smoke check" in `spec/exploration-results.md`, without passwords or tokens.
- If check 4 (`GET /api/v1/auth/users` with the staff1 token) returns 500, look in the CloudWatch logs of `cs361-g3-v2-auth` for `AccessDeniedException` on `cognito-idp:ListUsers`. Report what you find. No fallback, no IAM change, no code change. Registering and forwarding will also return 500 in that case.
- Check 5 (HEAD-1's existing documents are listed) can only pass after step E.
- Optional extras: check which `Access-Control-Allow-Origin` the deployed API returns for a foreign `Origin` (backlog S6), and whether the frontend's recipient list still loads now that `/api/v1/auth/users` needs a token.

**Step E, task 5.3 migration (approval required for `--apply`).**
- Dry run: `uv run scripts/migrate_assigned_to_sub.py`. It loads `.env` from the repo root and connects to the real database (read only) and to Cognito with your local AWS credentials. It prints one line per planned change, `UNRESOLVED <id> <title> [values]` lines, and `total=N unchanged=N to_update=N unresolved=N`. Exit codes: 0 ok, 1 unresolved documents exist, 2 configuration or backup error.
- Show the full output to the human and ask "Apply this migration now?".
- Apply: `uv run scripts/migrate_assigned_to_sub.py --apply --backup-file <durable path>`. The default backup goes to `/tmp`, which can be wiped, so give a durable path and keep the file. The backup is written before the first update and is never overwritten. Each update matches the old value, so `NOT MATCHED` means the document changed in between: run the dry run again.
- Unresolved documents are left unchanged. The human decides how to fix them, for example by reassigning through the admin UI.
- Restore (approval required): `uv run scripts/migrate_assigned_to_sub.py --restore <backup file>`. It needs only `MONGO_URI`.
- Then run smoke check 5.

**Step F, task 5.4.** Follow `spec/tasks.md`.

### 7.3 Deliberate differences found during execution (don't "fix" them)
- `extract_token` returns no token for an empty `Bearer ` header (the old helper returned `""`). Neither falls through to cookies.
- Migration: `--restore` doesn't need the pool id; the backup file is opened in exclusive-create mode; `--apply` with nothing to change writes no backup.
- 3.14 flow reorder and 3.17 README row, both approved by the owner.

### 7.4 Option: migrate before deploying (needs the owner's approval)
With the order in `spec/tasks.md` (deploy, then migrate), recipients can't open their existing documents between the deploy and the migration; admins still see everything. The old code also matches the caller's `sub` against `assigned_to` (requirement 1.11 in `spec/bugfix.md`), so migrated records keep working under the old code. The gap-free order is: dry run, `--apply`, deploy, smoke check, then dry run again and `--apply` again for documents created or forwarded in between. The script is idempotent, so the second run changes only those.

## 8. Intentional behavior changes (don't revert)

- `assigned_to` stores Cognito `sub` values, so the assign response returns subs. The frontend doesn't read that field.
- `receiver` no longer grants access. After a forward, the original receiver loses access; forwarding still replaces `assigned_to`.
- `GET /api/v1/auth/users` needs a verified token and returns `{"users": [{"id": <sub>, "name": <name>}]}`.
- A recipient that matches no user, or more than one by name, returns 400 instead of being stored as text.
- Login refuses with 503 when Cognito is unreachable.
- Expired tokens are rejected. Users must log in again when the Cognito ID token expires (1 hour by default, set on the app client), even though the cookie's `Max-Age` is still 24 hours. There is no refresh flow.

## 9. Known quirks

- Stale `__pycache__/*.pyc` files under `backend_v2/` still contain removed names. Filter grep with `--include`.
- `git diff` and `git log` may open a pager and hang a non-interactive shell. Use `git --no-pager`.
- In Kiro's shell tool, foreground `uv run` and heredoc commands sometimes returned exit code -1 with no output, although the command ran. Check the result (read the file, or run the command in the background with output to a log in `/tmp`) before running it again; a blind retry can duplicate appended text.
- Tests import helpers with `from conftest import ...`. `tests/` has no `__init__.py`; `pytest.ini` sets `pythonpath = .`. `scripts/` does have an `__init__.py`.
- Hypothesis writes `.hypothesis/`, which carries its own ignore file.
- `deploy_v2.py` and the migration script both load `.env` from the repo root.
- `set_use_cases(None)` resets a handler's cached use cases; `set_token_verifier(None)` resets the cached verifier.
- A new third-party dependency must go in `V2_DEPENDENCIES` in `aws_deploy/deploy_v2.py` (Lambda) and in `requirements-dev.txt` (tests). Lambda wheels must exist for `x86_64-manylinux2014` and Python 3.11. boto3 comes from the Lambda runtime.
- Cognito `ListUsers` errors are not caught by `CognitoUserDirectory`, so they surface as a generic 500.

## 10. Not done yet

- Not deployed. Smoke check not run. `LabRole` permission for `cognito-idp:ListUsers` not verified.
- Stored documents not migrated. Until they are, recipients of existing documents can't open them; admins can.
- Tasks 3.19 and 4 not finished as formal steps (see 7.1).
- The frontend still sends display names and uses a hard-coded recipient list in `frontend/html/lecturer_view.html`.
- Separate specs not started: system-generated document number, multi-recipient forwarding.
- Other security review findings, hardening and feature ideas: see [`backlog.md`](backlog.md).
