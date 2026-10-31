# Implementation Plan

## Overview

Fix the backend_v2 auth bypass (bugfix.md, findings 1 to 7 and 15) in this order: write the exploration test that fails on unfixed code (task 1), write preservation tests that pass on unfixed code (task 2), build the fix one layer at a time (tasks 3.1 to 3.17), re-run both test files (3.18, 3.19), run the full checkpoint (task 4), then deploy and migrate only with the user's approval (task 5).

## Notes

These are the rules for the executor. Read them before every task.

**Paths.** Every path below is relative to the project folder `CS361_G3/` (absolute: `/home/rumi/Works/antigravity/universityClasses/cs361/CS361_G3/`), unless it starts with `.kiro/`. Paths starting with `.kiro/` are in the workspace root (`/home/rumi/Works/antigravity/universityClasses/cs361/.kiro/`), outside the git repository. Run every command from `CS361_G3/` unless a task says otherwise.

**Test command.** Unless a task gives a narrower command, run tests with:

```
uv run --with-requirements requirements-dev.txt pytest -q
```

To run one file, append its path, for example `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_caller.py`. If `uv` cannot resolve or install the packages in `requirements-dev.txt`, stop and report the exact error. Do not switch to a global `pip install`.

**Source of truth.** `design.md` in this spec folder holds the full signatures, step lists and code sketches. When a task says "see design.md, section X", open that section and follow it exactly. When this file and design.md disagree, stop and report.

**Stop and report.** Stop work, do not improvise, and report to the user when any of these happens:
- A test that a task says must FAIL passes, or a test that must PASS fails and the cause is not a typo in the test you just wrote.
- The code you find does not match what a task describes (a function, constant, parameter or file is missing, named differently, or behaves differently).
- A command fails for a reason outside the task (network, missing tool, permissions).
- AWS returns `AccessDeniedException` for `cognito-idp:ListUsers` (task 5). Do not add a fallback. This is decision D4 in design.md.
- You would need to change a file not listed in the task to make a test pass.

**Do not invent.** No fallbacks, no retries, no extra features, no refactors of code a task does not name. Do not keep any old behavior "just in case" (no dev admin, no MongoDB login, no unsigned tokens, no alias matching).

**Out of scope. Do not edit these:**
- `frontend/` (everything under it)
- `backend/` (the v1 Flask backend)
- `aws_deploy/helpers/lambda_builder.py`, `aws_deploy/helpers/lambda_deployer.py`, `aws_deploy/helpers/auth.py`, `aws_deploy/helpers/frontend_deployer.py`, `aws_deploy/helpers/s3_storage.py`
- `aws_deploy/setup_cognito.py`, `aws_deploy/deploy.py`, `aws_deploy/destroy.py`, `aws_deploy/lambda_adapter.py`, `aws_deploy/lambda_handler.py`
- `requirements.txt`, `Dockerfile`, `.gitignore`, `.env`, `.env.example`
- `scripts/seed_mock_documents.py`, `scripts/seed_mock_users.py`
- Features handled by other specs: a system-generated document number, multi-recipient delivery or forwarding that keeps earlier recipients, CORS origin handling, cookie or token storage changes, upload content-type checks, rate limiting, access logging, deployment secrets, VPC.

**Agent files.** Never create agent files (notes, plans, scratch files, AGENTS.md) inside `CS361_G3/`. Notes go in `.kiro/specs/backend-v2-auth-bypass-fix/`. Temporary files go in `/tmp` and are deleted afterwards.

**Git.**
- Run git only from `CS361_G3/`.
- Do not commit unless the user asks. When the user asks, stage files by name (`git add path/one path/two`), never `git add -A` or `git add .`.
- Never stage `.kiro/`, `Reviews-fromAI`, `.env`, `lambda_v2_package.zip`, `.lambda_v2_build/` or any file outside `CS361_G3/`.
- Never push, force-push, reset, or rewrite history. Push only after the user approves.
- Before any commit, run `git status --short` and show the user the list of files you plan to stage.

**Deploy and data changes need the user's approval.** Do not run `aws_deploy/deploy_v2.py`, `scripts/migrate_assigned_to_sub.py --apply`, or `--restore` unless the user has said yes in this session for that exact step (task 5).

**Intermediate failures.** Tasks 3.6 to 3.12 change signatures one layer at a time. Between those tasks, the tests from tasks 1 and 2 and older handler code may fail or not import. That is expected. Each task's "done when" names the tests that must pass at that point. Tests from tasks 1 and 2 must all pass again at task 3.18 and 3.19.

## Tasks

- [x] 1. Write bug condition exploration test (with the shared test scaffolding)
  - **Property 1: Bug Condition** - Only verified Cognito identities and groups authorize requests
  - **CRITICAL**: This test MUST FAIL on unfixed code. Failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected behavior. It will validate the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the bug exists
  - **Scoped PBT Approach**: The bug is deterministic. Scope the property to the concrete failing cases listed below, plus one Hypothesis property over token defects
  - Confirm the starting point: `git log --oneline -1` must show `b52f483`. `git status --short` must show no changes under `backend_v2/`, `aws_deploy/` or `scripts/`. If either check fails, stop and report
  - Step A, scaffolding files (create these, all new):
    - `requirements-dev.txt` with exactly these lines: `pytest>=8.0`, `hypothesis>=6.100`, `pyjwt[crypto]>=2.10.1,<3`, `boto3>=1.28.0`, `pymongo>=4.3.0`
    - `pytest.ini` with exactly:
      ```
      [pytest]
      testpaths = tests
      pythonpath = .
      ```
    - Folder `tests/v2/`. Do NOT add `__init__.py` to `tests/` or `tests/v2/`. Do NOT name the folder `tests/backend_v2`
    - `tests/v2/conftest.py` with every fixture and helper in design.md, section "Testing Strategy > Validation Approach > Shared fixtures": `rsa_private_key` and `other_private_key` (session scope, 2048-bit RSA via `cryptography`), constants `TEST_POOL_ID = "us-east-1_TestPool1"`, `TEST_CLIENT_ID = "test-client-id"`, `TEST_KID = "test-kid-1"`, `TEST_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool1"`, `make_token(...)`, `make_none_token(claims)`, `StaticSigningKeyProvider` and its connection-error variant, the `verifier` fixture, the autouse `install_test_verifier` fixture, `InMemoryDocumentRepo`, `InMemoryFileStorage`, `FakeUserDirectory`, `build_document_use_cases`, `make_event`, `FakeCognitoClient`
    - In `install_test_verifier`, wrap `from backend_v2.shared.auth import set_token_verifier` in `try/except ImportError`. On unfixed code the import fails and the fixture only sets the two env vars with `monkeypatch.setenv`
    - In `FakeUserDirectory`, build the nine users from `SEED_USERS` in `aws_deploy/setup_cognito.py` (copy email and name values into the test file; do not import `setup_cognito.py`). Give each a fixed lowercase UUID `sub` such as `"00000000-0000-4000-8000-000000000001"`. Subclass `UserDirectoryPort` inside `try/except ImportError`
    - `make_event` must produce an API Gateway HTTP API v2 event: `{"requestContext": {"http": {"method": M}}, "rawPath": P, "headers": {...}, "cookies": [...], "queryStringParameters": {...}, "body": ..., "isBase64Encoded": False}`. Read how `lambda_handler` in `backend_v2/functions/documents/handler.py` reads method, path, headers and body, and match it. If the handler reads different keys, follow the handler and note it in the results file
    - Before using `InMemoryDocumentRepo`, read `backend_v2/shared/ports/document_repository.py` and implement every abstract method it declares
  - Step B, write `tests/v2/test_bug_condition_exploration.py`. Add the comment `# Feature: backend-v2-auth-bypass-fix, Property 1` at the top. It may only call `lambda_handler`, `set_use_cases()`, `DocumentUseCases`, `SearchUseCases`, `AuthUseCases` and the conftest helpers, so it runs unchanged before and after the fix. Write one test per case from design.md, section "Exploratory Bug Condition Checking > Test Cases":
    1. `make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})` to `GET /api/v1/documents` returns 401 with body `{"error": {"message": "Invalid authentication token"}}`
    2. Each of these to `GET /api/v1/search/trie` returns 401: a token signed by `other_private_key`, `exp` one hour ago, `aud="other-client"`, `iss` of another pool, `token_use="access"`
    3. A validly signed `USER` token with `name="Staff Helper"` and an email without "admin" or "staff" posting a valid multipart document to `POST /api/v1/documents` returns 403 with message `Admin privilege required to register documents`
    4. A `USER` token with `"custom:role": "admin"` calling `GET /api/v1/documents` does not see a document whose `assigned_to` holds another user's `sub`
    5. A `USER` token for `fac_a@tu.ac.th` (its own `sub`) with `name` set to FAC-B's display name, requesting `GET /api/v1/documents/<id>/file` for a document with `assigned_to=[<FAC-B sub>]` and `receiver=<FAC-B name>`, returns 403 with message `Access denied: You are not assigned to this document`
    6. `AuthUseCases(cognito_client=FakeCognitoClient(raising EndpointConnectionError), cognito_client_id="x")`, set into the auth handler with `set_use_cases()`, then `POST /api/v1/auth/login` with `admin@kanna.ac.th` / `admin1234`, returns 503 with message `Authentication service is unavailable`, no `token` key in the body, and no `Set-Cookie` header. If `AuthUseCases` has the attribute `_get_user_collection`, `monkeypatch.setattr` it on the instance to `lambda: None` first
    7. `FakeCognitoClient` returns a signed token for `admin9@tu.ac.th` with `cognito:groups=["USER"]`. The login response `user.role` equals `"user"`
    8. `GET /api/v1/auth/users` with no token returns 401 with message `Authentication is required`
    9. `GET /api/v1/auth/me` with an `alg: none` token returns 401 with message `Invalid authentication token`
    10. An admin-group signed token calling `GET /api/v1/documents` with a repo whose `find_all` raises `RuntimeError("secret-host:27017")` returns 500 with body exactly `{"error": {"message": "Internal server error"}}`
    11. Using `capsys` and `caplog`, an authenticated `GET /api/v1/documents` writes nothing that contains the caller's email or the text `[AUTH_DEBUG]`
    12. Hypothesis property, `@settings(max_examples=100, deadline=None)`: random claims (random `sub`, `email`, `name`, `cognito:groups` drawn from subsets of `["ADMIN", "USER", "STAFF", "admin"]`, optional `custom:role`) combined with one random defect (unsigned, foreign key, expired, wrong `aud`, wrong `iss`, `token_use="access"`). Every documents and search route returns 401, and a spy use case records zero calls
  - Step C, run on UNFIXED code: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_bug_condition_exploration.py`
  - **EXPECTED OUTCOME**: Every test in the file FAILS (this is correct, it proves the bug exists). Failures must be assertion failures (wrong status, wrong body, leaked text), not `ImportError`, `NameError` or fixture errors. Fix test-code errors only; never change code under `backend_v2/`
  - **STOP AND REPORT** if any of the 12 tests passes on unfixed code. Per design.md, that means the root cause analysis for that path is wrong
  - Document counterexamples: create `.kiro/specs/backend-v2-auth-bypass-fix/exploration-results.md` (workspace root, outside the repo). For each test, write the actual status code and body returned (shorten long bodies), for example "case 1: 200 with 3 documents instead of 401"
  - Mark task complete when the test is written, run, every case fails for the documented reason, and the results file exists
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15_

- [x] 2. Write preservation property tests (BEFORE implementing fix)
  - **Property 2: Preservation** - Legitimate requests behave as before
  - **IMPORTANT**: Follow observation-first methodology. Run each scenario on the UNFIXED code, read the real response, then write the assertion from what you saw
  - Non-bug inputs only (isBugCondition is false): every token is signed with `rsa_private_key`, has `iss=TEST_ISSUER`, `aud=TEST_CLIENT_ID`, `token_use="id"`, `exp` in the future; documents have `assigned_to` holding only lowercase `sub` values and `receiver` equal to the assigned user's display name; non-admin users have no `custom:role` or `role` claim, and their `email` and `name` never contain "admin" or "staff" (casefolded); admin users come from the seed users in group `ADMIN` (`staff1@tu.ac.th`, `admin1@tu.ac.th`, `admin2@tu.ac.th`)
  - Before writing generators, read `_parse_auth` in `backend_v2/functions/documents/handler.py` and confirm that an `ADMIN` group member is admin on unfixed code. If not, stop and report
  - Write `tests/v2/test_preservation.py` with `# Feature: backend-v2-auth-bypass-fix, Property 2` at the top. Hypothesis tests use `@settings(max_examples=100, deadline=None)`. Draw the token location from `"bearer"`, `"cookies"`, `"cookie_header"`. Write the seven cases from design.md, section "Preservation Checking > Test Cases":
    1. Admin: list returns every document; details 200; file 200 with `Content-Disposition` starting with `attachment` when `download=true` and `inline` otherwise, stored file name, `isBase64Encoded: true`; valid status change 200; assign 200
    2. Assigned `USER`: list and `GET /api/v1/search/trie` contain exactly the assigned subset; details and file 200; valid transitions from `ALLOWED_STATUS_TRANSITIONS` give 200, invalid ones give 400 with a message starting `Invalid workflow transition:`
    3. Non-assigned `USER`: details and file 403 `Access denied: You are not assigned to this document`; status 403 `Access denied: You cannot update an unassigned document`; assign 403 `Permission required to assign or forward this document`; the document is absent from list and trie
    4. Assign with `recipients=[<a seed user's sub>]`: `assigned_to` is replaced with that list, RECEIVED becomes ASSIGNED and ASSIGNED becomes FORWARDED, exactly one `status_history` entry is appended, and its `changed_by` equals the caller's email
    5. Login and logout: `FakeCognitoClient` returning a signed token gives 200, user keys exactly `id, email, name, role, faculty`, `role` from groups, and the cookie `auth_token=<token>; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400` in both `Set-Cookie` and `cookies`; `NotAuthorizedException` gives 401 `Invalid email or password`; empty email or password gives 400 `Email and password are required`; logout without a token gives 200 `{"message": "Logged out successfully"}` and a clearing cookie (copy its exact string from the unfixed response). If `AuthUseCases` has `_get_user_collection`, monkeypatch it to `lambda: None`
    6. Admin registers a document with `receiver` equal to a seed user's exact display name: 201 with the same `message`, `title`, `sender`, `receiver`, `status` (ASSIGNED) and history length as observed. Do NOT assert on `assigned_to` (intentional difference)
    7. Unknown id 404 `Document was not found: <id>`; bad JSON 400; non-multipart registration 400 `Content-Type must be multipart/form-data`; OPTIONS 204 with the same CORS headers as observed; no token 401 `Authentication is required`
  - Build document use cases with `build_document_use_cases(repo, storage, FakeUserDirectory())` so the same code runs before and after the fix
  - Run on UNFIXED code: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_preservation.py`
  - **EXPECTED OUTCOME**: All tests PASS (this confirms the baseline behavior to preserve)
  - **STOP AND REPORT** if a test fails on unfixed code and the failure is not a mistake in the test. Do not change `backend_v2/` code to make it pass
  - Append the observed values you hard-coded (cookie strings, CORS headers, messages) to `.kiro/specs/backend-v2-auth-bypass-fix/exploration-results.md` under a heading "Preservation observations"
  - Mark task complete when the tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.17_

- [ ] 3. Fix for unverified tokens, heuristic admin roles, fail-open login and name-based document access in backend_v2

  - [x] 3.1 Create the shared auth errors and `Caller` type
    - Create `backend_v2/shared/auth/errors.py` with constants `AUTH_REQUIRED_MESSAGE = "Authentication is required"`, `INVALID_TOKEN_MESSAGE = "Invalid authentication token"`, `SERVICE_UNAVAILABLE_MESSAGE = "Authentication service is unavailable"`, `INTERNAL_ERROR_MESSAGE = "Internal server error"`, and classes `AuthenticationError(PermissionError)` and `AuthServiceUnavailableError(Exception)`
    - Create `backend_v2/shared/auth/caller.py` with `ADMIN_GROUP = "ADMIN"`, the frozen dataclass `Caller(sub, email="", name="", groups=(), faculty="", claims={})` where `claims` uses `field(default_factory=dict, compare=False, repr=False, hash=False)`, properties `is_admin` (`ADMIN_GROUP in self.groups`, exact and case-sensitive), `role` (`"admin"` or `"user"`), `actor_label` (`self.email or self.sub`), and functions `caller_from_claims(claims)` and `require_admin(caller, message)`. Follow design.md, section "New package backend_v2/shared/auth/" exactly
    - `caller_from_claims` must raise `AuthenticationError(INVALID_TOKEN_MESSAGE)` when `sub` is missing or blank, use `cognito:groups` only when it is a list, and must NOT read `custom:role`, `role`, `cognito:username` or `username`
    - Do NOT add `__init__.py` to `backend_v2/` or `backend_v2/shared/`. Create `backend_v2/shared/auth/__init__.py` as an empty file for now (filled in task 3.3)
    - Write `tests/v2/test_caller.py`: field mapping, `is_admin` true only for exact `"ADMIN"` (false for `"admin"`, `"Admin"`, `"STAFF"`), `role` values, `actor_label` falls back to `sub`, missing or blank `sub` raises `AuthenticationError`, `custom:role="admin"` with no `ADMIN` group gives `role == "user"`, `repr(caller)` does not contain a claim value such as a test email placed only in `claims`
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_caller.py` passes
    - _Bug_Condition: C2 in isBugCondition (admin granted by text or role claims)_
    - _Expected_Behavior: admin only when "ADMIN" in verified cognito:groups (Property 3)_
    - _Requirements: 2.4, 2.5, 2.7_

  - [x] 3.2 Create `CognitoTokenVerifier`
    - Create `backend_v2/shared/auth/token_verifier.py` with class `CognitoTokenVerifier` (`ALGORITHM = "RS256"`), `__init__(self, user_pool_id, client_id, signing_key_provider=None, leeway_seconds=10)`, `from_env()`, and `verify(token) -> dict`. Follow design.md, section "File: backend_v2/shared/auth/token_verifier.py", including the seven `verify` steps in order
    - Configuration errors raise `RuntimeError("COGNITO_USER_POOL_ID is missing or malformed")` or `RuntimeError("COGNITO_CLIENT_ID is missing")`. Do NOT use `ValueError` (it would become a 400)
    - The production key provider is `jwt.PyJWKClient(self.jwks_url, cache_jwk_set=True, lifespan=3600, timeout=5)`
    - Every rejection raises `AuthenticationError(INVALID_TOKEN_MESSAGE)` with `from None`. An empty or non-string token raises `AuthenticationError(AUTH_REQUIRED_MESSAGE)`. `jwt.PyJWKClientConnectionError` raises `AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE)` and must be caught BEFORE `jwt.PyJWTError`
    - `jwt.decode` is called with `algorithms=["RS256"]`, `audience=client_id`, `issuer=self.issuer`, `leeway=self._leeway`, `options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]}`. Then reject when `claims.get("token_use") != "id"`
    - Write `tests/v2/test_token_verifier.py` covering every case in design.md, section "Unit Tests" for this file. For the HS256 case, build the token by hand with `hmac` and `hashlib.sha256`, using the PEM bytes of the public key as the secret, because `jwt.encode` refuses an asymmetric key for HS256. Assert that `str(exc)` equals the constant exactly (no library text)
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_token_verifier.py` passes
    - _Bug_Condition: C1 (token accepted although not a verified ID token)_
    - _Expected_Behavior: 401 "Invalid authentication token" for any unverified token; 503 when JWKS is unreachable_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Create `request_auth` and the package exports
    - Create `backend_v2/shared/auth/request_auth.py` with module variable `_verifier`, and functions `get_token_verifier()`, `set_token_verifier(verifier)`, `extract_token(event)`, `authenticate(event, verifier=None) -> Caller`, following design.md, section "File: backend_v2/shared/auth/request_auth.py"
    - `extract_token` must keep today's order. Copy the logic of `_extract_cookie_token` in `backend_v2/functions/documents/handler.py` (Bearer header in `authorization` or `Authorization`, then `event["cookies"]` item starting `auth_token=`, then the `cookie` or `Cookie` header). An empty `Bearer ` value returns `None`. If `_extract_cookie_token` checks a different order, stop and report
    - `authenticate` raises `AuthenticationError(AUTH_REQUIRED_MESSAGE)` when there is no token, BEFORE building the verifier
    - Fill `backend_v2/shared/auth/__init__.py` with the re-exports and `__all__` listed in design.md, section "File: backend_v2/shared/auth/__init__.py"
    - Write `tests/v2/test_request_auth.py`: each token location, the order when more than one is present, empty Bearer, and `authenticate` with no token and no `COGNITO_*` env vars (use `monkeypatch.delenv(..., raising=False)` and `set_token_verifier(None)`) raises `AuthenticationError` with `AUTH_REQUIRED_MESSAGE`
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_caller.py tests/v2/test_token_verifier.py tests/v2/test_request_auth.py` passes
    - _Preservation: token lookup order (3.5) and missing-token 401 (3.6)_
    - _Requirements: 2.1, 2.15, 3.5, 3.6_

  - [x] 3.4 Create `DirectoryUser` and recipient resolution
    - Create `backend_v2/shared/domain/directory_user.py` (standard library only) with the frozen dataclass `DirectoryUser(sub, email, name)` and functions `resolve_recipient(value, users)` and `resolve_recipients(values, users)`, following design.md, section "User directory (new domain type, port and adapter)"
    - Error messages, exactly: `Recipient must be a string`, `Recipient is required`, `Recipient name is ambiguous: <value>`, `Unknown recipient: <value>`, and for an empty list `Must assign at least one recipient user ID`. All are `ValueError`
    - Resolution order: exact `sub`, then email compared with `casefold()`, then a name (stripped, casefolded) that matches exactly one user
    - Add `DirectoryUser`, `resolve_recipient`, `resolve_recipients` to `backend_v2/shared/domain/__init__.py` imports and `__all__`. Do not change the other exports
    - Write `tests/v2/test_directory_user.py` covering the cases in design.md, section "Unit Tests"
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_directory_user.py` passes
    - _Requirements: 2.11, 2.12_

  - [x] 3.5 Create `UserDirectoryPort` and `CognitoUserDirectory`
    - Create `backend_v2/shared/ports/user_directory.py` with abstract class `UserDirectoryPort` and abstract method `list_users(self) -> list[DirectoryUser]`. Add it to `backend_v2/shared/ports/__init__.py` imports and `__all__`
    - Create `backend_v2/shared/adapters/cognito_user_directory.py` with `CognitoUserDirectory(user_pool_id=None, cognito_client=None, cache_ttl_seconds=300.0, clock=time.monotonic)`, following design.md exactly: `RuntimeError("COGNITO_USER_POOL_ID is missing")` when no pool id, `import boto3` inside `list_users`, region from `pool_id.split("_", 1)[0]`, paginate `list_users`, skip `Enabled is False` and users without `sub`, name fallback `attrs.get("name") or email.split("@")[0] or sub`, sort by `(name.casefold(), sub)`, cache for the TTL, return a copy. Do not catch boto errors
    - Add `CognitoUserDirectory` to `backend_v2/shared/adapters/__init__.py` imports and `__all__`. If importing it there pulls `boto3` at module import time, stop and report (it must be imported lazily inside `list_users`)
    - Write `tests/v2/test_cognito_user_directory.py`: a fake client whose `get_paginator("list_users").paginate(UserPoolId=...)` yields two pages; disabled users and users without `sub` are skipped; result is sorted; a second call within the TTL does not call the client again; after advancing the injected `clock` past the TTL the client is called again; missing pool id raises `RuntimeError`
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_cognito_user_directory.py` passes
    - _Requirements: 2.10, 2.11_

  - [x] 3.6 Change `Document.is_accessible_by` to sub-only matching
    - In `backend_v2/shared/domain/document.py`, replace the body and signature of `Document.is_accessible_by(self, user_id, is_admin=False)` with `is_accessible_by(self, user_sub: str, is_admin: bool = False) -> bool` exactly as in design.md, section "Domain and repository". Remove the lower-casing, the list handling and the `receiver` match. Do not change any other method
    - Write `tests/v2/test_document_access.py`: admin always true; exact `sub` in `assigned_to` true; different case false; matching `receiver` only false; a list argument false; empty or blank string false
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_document_access.py` passes
    - _Bug_Condition: C5 (document access through a non-sub identity)_
    - _Expected_Behavior: access only when verified sub is in assigned_to_
    - _Requirements: 2.11, 2.12_

  - [x] 3.7 Change `find_by_assigned_user` to an exact `sub` query
    - In `backend_v2/shared/ports/document_repository.py`, rename the `find_by_assigned_user` parameter to `user_sub: str` and set its docstring to `Retrieve all documents whose assigned_to list contains the given Cognito sub (exact match).`
    - In `backend_v2/shared/adapters/mongo_document_repository.py`, rewrite `MongoDocumentRepository.find_by_assigned_user(self, user_sub: str)`: return `[]` when `user_sub` is not a non-empty string (after `strip()`); otherwise query `self.collection.find({"assigned_to": user_sub.strip()})` and keep the existing `_to_domain` and skip-on-error loop. Delete the regex and `receiver` conditions. Delete `import re` only if nothing else in the file uses `re`
    - Write `tests/v2/test_mongo_repository_query.py`: set `repo._collection` to a fake that records the `find` filter and returns `[]`; `find_by_assigned_user("abc")` sends exactly `{"assigned_to": "abc"}`; `""`, `"  "`, `None` and `["abc"]` return `[]` with no `find` call. Construct `MongoDocumentRepository` without connecting (read its `__init__` first; if construction connects to MongoDB, stop and report)
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_mongo_repository_query.py` passes
    - _Requirements: 2.11, 2.12_

  - [x] 3.8 Update `DocumentUseCases` to use `sub` values and the user directory
    - In `backend_v2/functions/documents/use_cases.py`, change `DocumentUseCases` exactly as in design.md, section "File: backend_v2/functions/documents/use_cases.py": add `user_directory: Optional[UserDirectoryPort] = None` to `__init__`; add `_directory_users()` raising `RuntimeError("User directory is not configured")`; rewrite `register_document` (resolve the recipient BEFORE `save_file`, `receiver=recipient.name`, `assign_recipients([recipient.sub], assigned_by=uploaded_by, note="Assigned upon registration")`, keep the file rollback); remove the `recipient_ids` parameter
    - Change signatures to `list_documents(caller_sub, is_admin=False)`, `get_document_details(document_id, caller_sub, is_admin=False)`, `get_document_file(document_id, caller_sub, is_admin=False)`, `assign_document(document_id, recipients, caller_sub, actor, is_admin=False, note="")`, `update_document_status(document_id, new_status, caller_sub, actor, is_admin=False, note="")`
    - In `assign_document` keep this order: 404 lookup, then the 403 permission check, then `resolve_recipients`, then `assign_recipients`, then `repo.update` with the same payload keys as today
    - Keep these messages exactly: `Document was not found: <id>`, `Access denied: You are not assigned to this document`, `Access denied: You cannot update an unassigned document`, `Permission required to assign or forward this document`
    - Do not add tests in this task (the handler tests in 3.9 cover it)
    - Done when: `uv run --with-requirements requirements-dev.txt python -c "import backend_v2.functions.documents.use_cases"` exits 0, and the tests from 3.1 to 3.7 still pass
    - _Bug_Condition: C5_
    - _Expected_Behavior: authorization by caller_sub only; recipient names resolved to sub on the server (D5)_
    - _Preservation: 404 before 403, assign replaces assigned_to and appends one history entry (3.11, 3.12)_
    - _Requirements: 2.11, 2.12, 3.9, 3.10, 3.11, 3.12_

  - [x] 3.9 Rewrite authentication in the documents handler
    - In `backend_v2/functions/documents/handler.py`, DELETE: `_extract_cookie_token`, `_decode_jwt_payload`, `KNOWN_USER_ALIASES`, `_parse_auth`, the `[AUTH_DEBUG]` print, and any other `print` of identities, groups or roles. Keep `import base64`. Remove `Tuple` from the `typing` import only if it becomes unused
    - `get_use_cases()` builds `DocumentUseCases(repository=MongoDocumentRepository(), storage=S3FileStorageAdapter(), user_directory=CognitoUserDirectory())`. Keep the existing module globals pattern
    - Add `import logging` and `logger = logging.getLogger(__name__)`
    - In `lambda_handler`: keep the OPTIONS branch first and unchanged; move `use_cases = get_use_cases()` inside the `try`; then `caller = authenticate(event)`
    - Route changes as in design.md, section "File: backend_v2/functions/documents/handler.py" (list, register with `require_admin(caller, "Admin privilege required to register documents")` before the content-type check, assign with the `recipients must be a list of strings` check, status, file, details). Pass `uploaded_by=caller.actor_label` and `actor=caller.actor_label`
    - Replace the exception handling with exactly this order: `AuthenticationError` → 401 `str(err)`; `AuthServiceUnavailableError` → 503 `SERVICE_UNAVAILABLE_MESSAGE`; `PermissionError` → 403; `LookupError` → 404; `ValueError` → 400; `Exception` → `logger.exception("Unhandled error in documents handler")` and 500 `INTERNAL_ERROR_MESSAGE`. Remove every `f"Internal server error: ..."` and `f"Invalid authentication token: ..."`
    - Write `tests/v2/test_documents_handler.py` covering every documents row of design.md, section "HTTP status and message table", plus: register with an unknown recipient returns 400 `Unknown recipient: <value>` and `InMemoryFileStorage` holds no file afterwards; a verifier built with the connection-error key provider gives 503
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_documents_handler.py` passes, and `grep -n "AUTH_DEBUG\|_parse_auth\|_decode_jwt_payload\|KNOWN_USER_ALIASES" backend_v2/functions/documents/handler.py` prints nothing
    - _Bug_Condition: C1, C2, C5, C6, C7_
    - _Expected_Behavior: 401/403/500 per the HTTP status and message table; no identities in logs_
    - _Preservation: OPTIONS first (3.15), file disposition (3.14), validation messages (3.13), non-admin register 403 (3.8)_
    - _Requirements: 2.1, 2.4, 2.11, 2.13, 2.14, 3.7, 3.8, 3.13, 3.14, 3.15, 3.17_

  - [x] 3.10 Rewrite authentication in the search Lambda
    - In `backend_v2/functions/search/use_cases.py`, change `SearchUseCases.get_search_trie` to `get_search_trie(self, caller_sub: str, is_admin: bool = False)` and pass `caller_sub` to `find_by_assigned_user`. Keep the rest of the body
    - In `backend_v2/functions/search/handler.py`, DELETE `_extract_cookie_token`, `_decode_jwt_payload`, `KNOWN_USER_ALIASES`, `_parse_auth` and `import base64` (confirm with grep that `base64` has no other use first). Use `caller = authenticate(event)` and `get_search_trie(caller_sub=caller.sub, is_admin=caller.is_admin)`
    - Exception order: `AuthenticationError` → 401; `AuthServiceUnavailableError` → 503; `PermissionError` → 403; `Exception` → `logger.exception("Unhandled error in search handler")` and 500 `INTERNAL_ERROR_MESSAGE`. No `LookupError` or `ValueError` branches. Keep OPTIONS first
    - Write `tests/v2/test_search_handler.py`: no token 401, invalid token 401, admin sees all documents in the trie, `USER` sees only documents with their `sub`, unhandled error 500 with the generic body
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_search_handler.py` passes, and `grep -n "_parse_auth\|_decode_jwt_payload\|KNOWN_USER_ALIASES" backend_v2/functions/search/handler.py` prints nothing
    - _Bug_Condition: C1, C2, C5, C6_
    - _Expected_Behavior: same checks as the documents handler_
    - _Requirements: 2.1, 2.2, 2.4, 2.11, 2.13, 3.7, 3.9, 3.10_

  - [x] 3.11 Rewrite `AuthUseCases` to talk only to Cognito
    - In `backend_v2/functions/auth/use_cases.py`, DELETE: `_decode_jwt_payload`, `_get_user_collection`, the MongoDB fallback block in `login`, the `admin@kanna.ac.th` / `admin1234` branch, `_synthesize_test_token`, the `email.startswith("admin")` role rule and hard-coded admin email list, the hard-coded name set in `list_users`, the `AccessToken` fallback, and `import base64` / `import json` if unused
    - Add `INVALID_CREDENTIAL_CODES = frozenset({"NotAuthorizedException", "UserNotFoundException", "UserNotConfirmedException", "PasswordResetRequiredException"})`
    - New `__init__(self, cognito_client=None, cognito_client_id=None, token_verifier=None, user_directory=None)`. Keep reading `COGNITO_CLIENT_ID` from the environment when `cognito_client_id` is `None` (check how the current `__init__` does it and keep that)
    - `_get_cognito_client()` creates `boto3.client("cognito-idp", region_name=os.getenv("AWS_REGION", "us-east-1"))` lazily and wraps any exception as `AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None`
    - Rewrite `login` with the eight steps in design.md, section "File: backend_v2/functions/auth/use_cases.py". Catch `botocore.exceptions.ClientError` by its `Error.Code`, not by string matching. Bad credentials raise `PermissionError("Invalid email or password")`. Any other Cognito failure raises `AuthServiceUnavailableError`. A Cognito token that fails verification is logged with `logger.error("Cognito returned a token that failed verification")` and raises `AuthServiceUnavailableError`
    - `list_users()` returns `[{"id": u.sub, "name": u.name} for u in self._directory.list_users()]`, and raises `RuntimeError("User directory is not configured")` when there is no directory
    - Logging: only Cognito error codes and exception class names. Never log email, password, token or claims
    - Done when: `grep -n "mock_signature\|admin1234\|_synthesize_test_token\|_get_user_collection\|password_hash" backend_v2/functions/auth/use_cases.py` prints nothing, and `uv run --with-requirements requirements-dev.txt python -c "import backend_v2.functions.auth.use_cases"` exits 0
    - _Bug_Condition: C2 (login role heuristic), C3 (login without Cognito authentication)_
    - _Expected_Behavior: 401 for bad credentials, 503 when Cognito cannot be reached, role only from cognito:groups, only Cognito-issued tokens_
    - _Preservation: login response shape (3.1), 401 message (3.2), 400 on empty fields (3.3)_
    - _Requirements: 2.3, 2.6, 2.7, 2.8, 2.9, 2.10, 3.1, 3.2, 3.3_

  - [x] 3.12 Rewrite authentication in the auth handler
    - In `backend_v2/functions/auth/handler.py`, DELETE `_extract_cookie_token`, `_decode_jwt_payload` and `import base64` (confirm no other use with grep first)
    - `get_use_cases()` builds `AuthUseCases(user_directory=CognitoUserDirectory())`
    - Leave the login and logout branches unchanged (same bodies and cookie strings)
    - `GET /api/v1/auth/users`: `authenticate(event)` first (any authenticated user), then `_json_response(200, {"users": use_cases.list_users()})`
    - `GET /api/v1/auth/me`: `caller = authenticate(event)`, then `_json_response(200, {"user": caller.claims})`
    - Exception order: `AuthenticationError` → 401 `str(err)`; `AuthServiceUnavailableError` → 503 `SERVICE_UNAVAILABLE_MESSAGE`; `PermissionError` → 401 `str(err)`; `ValueError` → 400; `Exception` → `logger.exception("Unhandled error in auth handler")` and 500 `INTERNAL_ERROR_MESSAGE`
    - Write `tests/v2/test_auth_handler.py` covering every auth row of the HTTP status and message table, plus: each `INVALID_CREDENTIAL_CODES` code gives 401 `Invalid email or password`; a `ClientError` with code `InternalErrorException` gives 503; `EndpointConnectionError` gives 503; a response with `ChallengeName` and no `AuthenticationResult` gives 401; `/auth/users` for a `USER` caller returns `{"users": [{"id": ..., "name": ...}, ...]}`; `/auth/me` returns the verified claims; logout needs no token
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_auth_handler.py` passes, and `grep -rn "_decode_jwt_payload\|_parse_auth\|KNOWN_USER_ALIASES\|AUTH_DEBUG\|mock_signature" backend_v2/` prints nothing
    - _Bug_Condition: C1 (/auth/me), C3, C4, C6_
    - _Expected_Behavior: 401 for /auth/users and /auth/me without a verified token; generic 500_
    - _Preservation: login and logout responses unchanged (3.1, 3.2, 3.3, 3.4)_
    - _Requirements: 2.8, 2.9, 2.10, 2.13, 2.15, 3.1, 3.2, 3.3, 3.4, 3.6_

  - [x] 3.13 Write the role consistency property test
    - **Property 3: Role consistency** - One role rule everywhere
    - Write `tests/v2/test_role_consistency.py` with `# Feature: backend-v2-auth-bypass-fix, Property 3` and `@settings(max_examples=100, deadline=None)`
    - Generate random verified claims: random `sub`, random `email` and `name` (allow text containing "admin" and "staff"), `cognito:groups` as a random subset of `["ADMIN", "USER", "STAFF", "admin", "Admin"]`, optional `custom:role` and `role` set to `"admin"`, optional `cognito:username`
    - For each, assert all three are equal to `"ADMIN" in groups`: (a) `user["role"] == "admin"` from `AuthUseCases.login` with a `FakeCognitoClient` returning that token, (b) whether the documents handler returns an unassigned document in `GET /api/v1/documents`, (c) `caller_from_claims(claims).role == "admin"`
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_role_consistency.py` passes
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

  - [x] 3.14 Write the offline integration tests
    - Write `tests/v2/test_integration_flows.py` with the three flows in design.md, section "Integration Tests": the full routing flow (admin registers to "อ.ดร.ปกรณ์ สุขใจ" by name, HEAD-1 lists and acknowledges, forwards to FAC-B by name, FAC-B lists and downloads, HEAD-1 then gets 403), token transport through all three locations including the `auth_token` cookie value from the login response, and cross-Lambda admin consistency
    - Use the real `CognitoTokenVerifier` with `StaticSigningKeyProvider`, the real use cases, and the in-memory adapters. Look up the valid acknowledge transition in `ALLOWED_STATUS_TRANSITIONS` in `backend_v2/shared/domain/` instead of guessing the status name
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_integration_flows.py` passes
    - _Requirements: 2.11, 3.5, 3.9, 3.10, 3.11_

  - [x] 3.15 Write the one-time migration script and its tests
    - **Property 4: Migration** - Legacy recipients keep access, and only them
    - Create `scripts/migrate_assigned_to_sub.py` following design.md, section "One-time migration script" exactly: the uv inline-script header with `boto3>=1.28.0`, `pymongo>=4.3.0`, `dnspython>=2.3.0`; `sys.path` setup like `aws_deploy/deploy_v2.py`; imports of `load_env_file` and `normalize_mongo_uri` from `aws_deploy.helpers.auth` and of `DirectoryUser`, `resolve_recipient` from `backend_v2.shared.domain.directory_user`; the `LEGACY_ALIAS_TO_EMAIL` table copied exactly; pure functions `load_directory_users`, `legacy_values`, `resolve_legacy_identity`, `plan_migration`; dataclasses `PlannedChange`, `UnresolvedDoc`, `MigrationPlan`; argparse options `--apply`, `--backup-file PATH`, `--restore PATH` (mutually exclusive with `--apply`), `--user-pool-id ID`
    - Do NOT call `get_mongo_uri()` (it prompts). Missing `MONGO_URI` or pool id: print an error, exit 2. Exit 1 when any document is unresolved, 0 otherwise. Default backup path `/tmp/assigned_to_backup_<UTC yyyymmddTHHMMSSZ>.json`
    - Apply writes the backup file BEFORE the first `update_one`, and each update filters on `{"_id": ObjectId(doc_id), "assigned_to": old_raw}`. Never modify `receiver` or `status_history`
    - All Mongo and Cognito calls happen inside `main()` or functions it calls, never at import time, so tests can import the module
    - Write `tests/v2/test_migration_plan.py` with `# Feature: backend-v2-auth-bypass-fix, Property 4`: the unit cases in design.md, section "Unit Tests" for this file (import with `from scripts.migrate_assigned_to_sub import ...`; use a fake collection that records calls), plus the Hypothesis property in design.md, section "Property-Based Tests > Property 4" (correct `sub` per value, unknown or ambiguous values reported and not changed, idempotence)
    - Do NOT run the script against a real database in this task
    - Done when: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_migration_plan.py` passes
    - _Preservation: legacy recipients keep access after migration (3.16)_
    - _Requirements: 2.11, 2.12, 3.16_

  - [x] 3.16 Update the v2 deployment script
    - In `aws_deploy/deploy_v2.py`: append `"pyjwt[crypto]>=2.10.1,<3"` to `V2_DEPENDENCIES` (leave the other four entries); in `build_v2_package()` replace `"--python-platform", "linux"` with `"--python-platform", "x86_64-manylinux2014"` and add `"--only-binary", ":all:"` to the same `cmd` list; in `main()`, right after `session, account_id, region = get_aws_session()`, add a check that prints `error: COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID must be set. Run aws_deploy/setup_cognito.py first.` and calls `sys.exit(1)` when either env var is empty (add `import sys` if missing)
    - Do NOT change anything else in the file, and do NOT run it
    - Verify the build flags without deploying: `uv pip install --dry-run --python-version 3.11 --python-platform x86_64-manylinux2014 --only-binary :all: "pyjwt[crypto]>=2.10.1,<3" "pymongo>=4.3.0" "dnspython>=2.3.0" "python-dotenv>=1.0.0" "werkzeug>=2.3.0" --target /tmp/v2_build_check`. It must resolve with no error. If `--dry-run` is not supported together with `--target`, run it without `--dry-run` and then delete `/tmp/v2_build_check`. If resolution fails, stop and report the error
    - Done when: the resolve check succeeds and `uv run --with-requirements requirements-dev.txt python -c "import ast,sys; ast.parse(open('aws_deploy/deploy_v2.py').read())"` exits 0
    - _Requirements: 2.1, 2.3_

  - [x] 3.17 Update `backend_v2/README.md`
    - In the "Routing Matrix": the `/api/v1/auth/users` row becomes "Authenticated", and the `/assign` row becomes "ADMIN or assigned user"
    - Under "Security & Compliance Highlights", add one sentence: each Lambda verifies the Cognito ID token (RS256 signature against the user pool JWKS, issuer, audience, expiry and `token_use`) before any operation
    - Change nothing else. If either section or row does not exist, stop and report
    - _Requirements: 2.1, 2.10_

  - [x] 3.18 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Only verified Cognito identities and groups authorize requests
    - **IMPORTANT**: Re-run the SAME test from task 1. Do NOT write a new test and do NOT edit `tests/v2/test_bug_condition_exploration.py`
    - The test from task 1 encodes the expected behavior. When it passes, the expected behavior is satisfied
    - Run: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_bug_condition_exploration.py`
    - **EXPECTED OUTCOME**: All tests PASS (confirms the bug is fixed)
    - **STOP AND REPORT** if any test still fails, with the test name and the failure output. Do not change the test to make it pass
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15_

  - [ ] 3.19 Verify preservation tests still pass
    - **Property 2: Preservation** - Legitimate requests behave as before
    - **IMPORTANT**: Re-run the SAME tests from task 2. Do NOT write new tests and do NOT edit `tests/v2/test_preservation.py`
    - Run: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_preservation.py`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions)
    - **STOP AND REPORT** if any test fails. The only allowed behavior differences are the four listed in design.md, section "Intentional differences that are not regressions". If a failure matches one of them, report it to the user before changing the test
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.17_

- [ ] 4. Checkpoint - Ensure all tests pass
  - Run the full suite: `uv run --with-requirements requirements-dev.txt pytest -q`. Every test must pass
  - Run `grep -rn "_decode_jwt_payload\|_parse_auth\|KNOWN_USER_ALIASES\|AUTH_DEBUG\|mock_signature\|_synthesize_test_token\|admin1234\|Internal server error: " backend_v2/`. It must print nothing
  - Run `git status --short` and check that changed and new files are limited to: `backend_v2/shared/auth/` (new), `backend_v2/shared/domain/directory_user.py` (new), `backend_v2/shared/domain/__init__.py`, `backend_v2/shared/domain/document.py`, `backend_v2/shared/ports/user_directory.py` (new), `backend_v2/shared/ports/__init__.py`, `backend_v2/shared/ports/document_repository.py`, `backend_v2/shared/adapters/cognito_user_directory.py` (new), `backend_v2/shared/adapters/__init__.py`, `backend_v2/shared/adapters/mongo_document_repository.py`, `backend_v2/functions/*/handler.py`, `backend_v2/functions/*/use_cases.py`, `backend_v2/README.md`, `aws_deploy/deploy_v2.py`, `scripts/migrate_assigned_to_sub.py` (new), `tests/v2/` (new), `requirements-dev.txt` (new), `pytest.ini` (new). If anything else changed, stop and report. Delete stray `__pycache__` only if git shows it (it is ignored, so it should not appear)
  - Report to the user: the test summary line, the list of changed files, and the exploration results file path. Ask whether they want a local commit, and ask before starting task 5
  - Ensure all tests pass, ask the user if questions arise

- [ ] 5. Deploy, check `ListUsers`, and migrate data (ONLY with the user's explicit approval for each step)
  - [ ] 5.1 Deploy backend_v2 (needs user approval)
    - Ask the user: "Deploy backend_v2 now with `uv run aws_deploy/deploy_v2.py`?" Continue only on a clear yes
    - Run from `CS361_G3/`: `uv run aws_deploy/deploy_v2.py`. The script may prompt for the MongoDB URI; let the user answer it. If it fails, stop and report the output. Do not retry with changed flags
    - Record the API endpoint the script prints
    - _Requirements: 2.1, 2.3_

  - [ ] 5.2 Run the post-deployment smoke check
    - Using `curl` against the recorded endpoint, run the six checks in design.md, section "Integration Tests > Post-deployment smoke check" and record each status and body in `.kiro/specs/backend-v2-auth-bypass-fix/exploration-results.md` under "Smoke check". Use passwords from `aws_deploy/setup_cognito.py` `SEED_USERS`; do not write passwords or tokens into the results file
    - **STOP AND REPORT** if check 4 (`GET /api/v1/auth/users` with the staff1 token) returns 500. Look in CloudWatch logs for the `cs361-g3-v2-auth` function for `AccessDeniedException` on `cognito-idp:ListUsers`. Report what you found. Do NOT add a fallback, do NOT change IAM, do NOT edit code. This is decision D4
    - Check 5 (HEAD-1's existing documents still listed) can only pass after task 5.3. Run it after 5.3
    - _Requirements: 2.1, 2.3, 2.10, 2.14_

  - [ ] 5.3 Migrate stored `assigned_to` values to Cognito `sub` (dry run, then apply with approval)
    - Dry run (no writes, allowed without extra approval once 5.1 was approved): `uv run scripts/migrate_assigned_to_sub.py`. Show the user the full output, including the summary line `total=N unchanged=N to_update=N unresolved=N` and every unresolved document
    - Ask the user: "Apply this migration now?" Continue only on a clear yes
    - Apply: `uv run scripts/migrate_assigned_to_sub.py --apply`. Report the backup file path it prints, the number of matched updates, and any updates that did not match. Tell the user to keep the backup file
    - Unresolved documents stay unchanged and become admin-only until an admin re-assigns them through `POST /api/v1/documents/<id>/assign`. Report them; do not try to fix them yourself
    - Only on the user's explicit request: `uv run scripts/migrate_assigned_to_sub.py --restore <backup path>`
    - Then run smoke check 5 from task 5.2 and record the result
    - _Requirements: 3.16, 2.11_

  - [ ] 5.4 Hand over
    - Report to the user: smoke check results, migration summary, backup path, and unresolved documents
    - Do not push. If the user asks for a commit or push, follow the Git rules at the top of this file
