# Exploration Results (Task 1)

Run on the UNFIXED code at commit `b52f483`, no changes under `backend_v2/`.

Command: `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_bug_condition_exploration.py`

Result: `12 failed`. Every failure is an `AssertionError` on the response (status, body, or captured output). There is no ImportError, NameError or fixture error. The fixed-only import in `install_test_verifier` fails with ImportError as planned and the fixture falls back to setting only the two env vars.

The root cause analysis holds for all 12 paths: none of the tests passed.

## Counterexamples

| # | Test | Actual result on unfixed code | Expected after fix |
|---|------|-------------------------------|--------------------|
| 1 | `test_unsigned_admin_token_is_rejected_on_documents` | `alg: none` token with `cognito:groups=["ADMIN"]` to `GET /api/v1/documents`: 200 with the full document list | 401 `Invalid authentication token` |
| 2 | `test_foreign_expired_wrong_aud_wrong_iss_and_access_tokens_are_rejected_on_search` | `GET /api/v1/search/trie` returns 200 for all five tokens: foreign key 200, expired 200, wrong aud 200, wrong iss 200, `token_use=access` 200 | 401 for each |
| 3 | `test_display_name_staff_does_not_grant_admin_for_registration` | USER token with `name="Staff Helper"` and email `helper@example.com`: `POST /api/v1/documents` returns 201 `Document registered successfully` | 403 `Admin privilege required to register documents` |
| 4 | `test_custom_role_admin_claim_does_not_grant_access_to_other_users_documents` | `fac_a` USER token with `custom:role=admin`: list returns `['doc-1', 'doc-2']`, including `doc-2` assigned to head1's `sub` | only `doc-1` (own document) |
| 5 | `test_copying_another_users_display_name_does_not_grant_their_file` | `fac_a` token with `name` set to FAC-B's name: `GET /api/v1/documents/doc-1/file` returns 200 with the file body (`JVBERi0xLjQgdGVzdA==`) for a document assigned to FAC-B's `sub` | 403 `Access denied: You are not assigned to this document` |
| 6 | `test_login_fails_closed_when_cognito_is_unreachable` | `EndpointConnectionError` from Cognito, `admin@kanna.ac.th` / `admin1234`: 200, user `{"id": "mock-admin-id", "role": "admin", ...}`, token with header `{"alg":"none","typ":"JWT"}` (`.mock_signature`) | 503 `Authentication service is unavailable`, no token, no cookie |
| 7 | `test_login_role_comes_only_from_cognito_groups` | Signed token for `admin9@tu.ac.th` with `cognito:groups=["USER"]`: login returns 200 with `user.role == "admin"` | `user.role == "user"` |
| 8 | `test_auth_users_requires_authentication` | `GET /api/v1/auth/users` with no token: 200 `{"users": [{"name": "Kasidit"}, {"name": "Prapaporn"}, ...]}` (hard-coded names) | 401 `Authentication is required` |
| 9 | `test_auth_me_rejects_unsigned_token` | `alg: none` token to `GET /api/v1/auth/me`: 200 `{"user": {"sub": "attacker", "cognito:groups": ["ADMIN"]}}` | 401 `Invalid authentication token` |
| 10 | `test_unhandled_error_returns_generic_500` | Admin call with `find_all` raising `RuntimeError("secret-host:27017")`: 500 `{"error": {"message": "Internal server error: secret-host:27017"}}` | 500 `{"error": {"message": "Internal server error"}}` |
| 11 | `test_authenticated_request_writes_no_identity_to_stdout_or_logs` | Captured stdout contains `[AUTH_DEBUG] identities=['head1@tu.ac.th', 'อ.ดร.ปกรณ์ สุขใจ', '00000000-0000-4000-8000-000000000002', ...], is_admin=False, groups=['USER'], role=` | nothing containing the caller's email or `[AUTH_DEBUG]` |
| 12 | `test_any_token_defect_is_rejected_on_every_documents_and_search_route` | Hypothesis found a failing case on the first examples: `defect='unsigned'`, `via='bearer'`, claims with `cognito:groups=[]`. No route returned 401. `GET /api/v1/documents`, details, file, assign, status and `GET /api/v1/search/trie` all reached the spy use case (`spy.calls == ['list_documents', 'get_document_details', 'get_document_file', 'assign_document', 'update_document_status', 'get_search_trie']`); they returned 500 only because the spy returns `None`. `POST /api/v1/documents` returned 403 (treated as a non-admin) instead of 401 | 401 on every route, zero use case calls |

## Notes on the scaffolding

- `make_event` matches how `lambda_handler` reads events: method from `requestContext.http.method`, path from `rawPath`, headers (`authorization`, `cookie`, `content-type` lowercased), `cookies` list, `queryStringParameters`, `body`, `isBase64Encoded`. No deviation from the handler.
- `InMemoryDocumentRepo` implements all six abstract methods of `DocumentRepositoryPort` (`get_by_id`, `find_all`, `find_by_assigned_user`, `create`, `update`, `delete`) and stores deep copies so that `update` is what persists changes.
- Test 12 uses `suppress_health_check=[HealthCheck.function_scoped_fixture]` in addition to `max_examples=100, deadline=None`, because the autouse `install_test_verifier` fixture is function scoped. The fixture state does not depend on the generated example.
- The `app` and `auth_login` fixtures (extra helpers in `conftest.py`) wire in-memory adapters into the handlers through `set_use_cases()` and reset them to `None` after each test.
- Hypothesis writes its example database to `CS361_G3/.hypothesis/`. It is not shown by `git status` (Hypothesis puts its own ignore file there).

## Preservation observations

Task 2. Observed on the unfixed code (commit `b52f483`) with `tests/v2/test_preservation.py`, then hard-coded into the tests. Result on unfixed code: 34 passed (3 runs, including `--hypothesis-seed=1` and `=2`). No file under `backend_v2/` was changed. `conftest.py` and the exploration test were not changed.

Precondition check: `_parse_auth` in `documents/handler.py` marks a token with `cognito:groups=["ADMIN"]` as admin (`"ADMIN" in groups`). Seed admins `staff1@tu.ac.th`, `admin1@tu.ac.th`, `admin2@tu.ac.th` all return `is_admin=True` in the `[AUTH_DEBUG]` line. Seed non-admins (`head1`, `fac_a`, `fac_b`, `user1`, `user2`, `user3`) return `is_admin=False`.

### Cookie strings (auth Lambda)
- Login success, both `headers["Set-Cookie"]` and `cookies[0]`: `auth_token=<IdToken>; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400`
- Logout (also when a token is sent), both `headers["Set-Cookie"]` and `cookies[0]`: `auth_token=; Path=/; HttpOnly; Secure; SameSite=None; Max-Age=0`
- Error responses (401, 400) carry no `Set-Cookie` header and no `cookies` key.

### Auth responses
| Request | Status | Body |
|---|---|---|
| login, Cognito returns signed ID token | 200 | `{"user": {"id": <sub>, "email", "name", "role", "faculty"}, "token": <IdToken>}`, key set of `user` is exactly `id, email, name, role, faculty`. `role` is `admin` for group ADMIN and `user` for USER. `faculty` is `custom:faculty`. |
| login, `NotAuthorizedException` | 401 | `{"error": {"message": "Invalid email or password"}}` |
| login, `UserNotFoundException` | 401 | `{"error": {"message": "Invalid email or password"}}` |
| login, empty or whitespace email, empty password, or either key missing | 400 | `{"error": {"message": "Email and password are required"}}` |
| logout, no token | 200 | `{"message": "Logged out successfully"}` |
| `GET /api/v1/auth/me`, no token | 401 | `{"error": {"message": "Authentication is required"}}` |

`initiate_auth` receives `AuthParameters={"USERNAME": <email stripped and lower-cased>, "PASSWORD": <password>}`. Login with an upper-case or padded email still succeeds and sends the normalized email.

### CORS headers on OPTIONS (204, empty body, no token needed)
Common to all three Lambdas: `Content-Type: application/json`, `Access-Control-Allow-Origin: <request origin header, else FRONTEND_ORIGIN env, else *>`, `Access-Control-Allow-Credentials: true`, `Access-Control-Allow-Headers: Content-Type,Authorization,Cookie`.

| Lambda | `Access-Control-Allow-Methods` |
|---|---|
| documents | `GET,POST,PATCH,PUT,DELETE,OPTIONS` |
| search | `GET,OPTIONS` |
| auth | `GET,POST,OPTIONS` |

The test removes `FRONTEND_ORIGIN` with `monkeypatch.delenv` and checks both no origin (`*`) and `Origin: https://app.example` (echoed).

### Documents and search responses
| Case | Status | Observed value |
|---|---|---|
| no token (documents, search) | 401 | `Authentication is required` |
| non-admin details or file of a document not assigned to them | 403 | `Access denied: You are not assigned to this document` |
| non-admin status change on a document not assigned to them | 403 | `Access denied: You cannot update an unassigned document` |
| non-admin assign or forward on a document not assigned to them | 403 | `Permission required to assign or forward this document` |
| non-admin `POST /api/v1/documents` | 403 | `Admin privilege required to register documents` |
| unknown id: details, file, status, assign (admin and non-admin; 404 comes before the 403 check) | 404 | `Document was not found: <id>` |
| invalid transition (status already ACKNOWLEDGED, target ASSIGNED) | 400 | `Invalid workflow transition: Cannot transition from ACKNOWLEDGED to ASSIGNED` |
| bad JSON body on status or assign | 400 | the `json.loads` error text, for example `Expecting property name enclosed in double quotes: line 1 column 2 (char 1)`. The test computes the expected text with `json.loads` so it does not depend on the Python version. |
| assign with `recipients: []` | 400 | `Must assign at least one recipient user ID` |
| registration, content type not multipart | 400 | `Content-Type must be multipart/form-data` |
| registration, no `receiver` field | 400 | `Missing required fields: receiver` |
| registration, multipart with no file | 400 | `Missing uploaded file` |
| non-admin list and trie with nothing assigned | 200 | `{"documents": [], "count": 0}` and `{"trie": {}, "count": 0}` |
| admin assign RECEIVED document | 200 | `{"message": "Document assigned successfully", "status": "ASSIGNED", "assigned_to": [...]}` (status ASSIGNED, history +1, `changed_by` = admin email) |
| assigned user forwards ASSIGNED document | 200 | status `FORWARDED`, `assigned_to` replaced, history +1, `changed_by` = caller email |
| status change | 200 | `{"message": "Status updated successfully", "status": "<new status>"}` |
| `ASSIGNED` document, status targets | | `ACKNOWLEDGED` and `FORWARDED` give 200. `RECEIVED`, `ASSIGNED`, `IN_PROGRESS`, `COMPLETED` give 400 `Invalid workflow transition: ...` |

### File responses
- `isBase64Encoded: true`, base64 body, `Content-Type` = stored file type (`application/pdf`).
- `Content-Disposition: inline; filename="<stored name>"` without `download=true`.
- `Content-Disposition: attachment; filename="<stored name>"` with `download=true`.
- Checked with the file names `a.pdf`, `สำคัญ.pdf` and `report 2025.pdf`.

### Registration by an admin
Receiver set to a seed user's exact display name. Response 201, `message` `Document registered successfully`, `document.title`, `sender`, `receiver` as sent, `status` `ASSIGNED`, `uploaded_by` = admin email, `file_name` `letter.pdf`, `status_history` length 2 (`RECEIVED` then `ASSIGNED`, last `changed_by` = admin email). The test does not assert `assigned_to`: the unfixed code stores the display name there, the fix will store the `sub`.

### Token lookup (Req 3.5)
Bearer header beats a cookie that holds garbage. Both `authorization` and `Authorization` header names work. `event["cookies"]` entries with attributes (`auth_token=<t>; Path=/; HttpOnly`) and a `Cookie` header with other cookies (`theme=dark; auth_token=<t>; lang=th`) both authenticate.

### Notes on the tests
- Each token location (`bearer`, `cookies`, `cookie_header`) is drawn by Hypothesis in the document tests.
- Non-admin users are either the six seed non-admins or generated users (random uuid `sub`, `gen.<local>@tu.ac.th`, `gen <text>`), none containing "admin" or "staff" after `casefold()`. Within one scenario, users are de-duplicated on `sub`, email and display name. Two test mistakes in my first run were fixed this way: two generated users with the same name (the unfixed code matches `receiver` by name, so that is the name-collision case and outside the preserved inputs), and a note with a trailing space (the domain strips notes).
- `test_admin_has_full_access` draws at least two documents so that the status change and the assign act on different documents (a status of ACKNOWLEDGED cannot be forwarded afterwards).
- Hypothesis tests reuse the function-scoped `app` fixture. `reset(app)` clears the repo at the start of each example, and `HealthCheck.function_scoped_fixture` is suppressed, as in the exploration test.
- `[AUTH_DEBUG]` lines and `Cognito login error` lines print to stdout during these runs on the unfixed code. The tests do not look at them.
- The intentional differences listed in design.md (`assigned_to` holding subs, no `receiver` access, `/auth/users` shape) are not asserted.

## After the fix (task 3.18)

Re-ran the same, unedited `tests/v2/test_bug_condition_exploration.py` against the fixed code with `uv run --with-requirements requirements-dev.txt pytest -q tests/v2/test_bug_condition_exploration.py`. The file has 12 tests with the same names as the table above. Its last change (mtime 10:26) is before the preservation tests (task 2) were written; `git status --short` shows it only as untracked (`??`), with nothing else to compare against.

| # | Test | Result |
|---|------|--------|
| 1 | `test_unsigned_admin_token_is_rejected_on_documents` | PASS |
| 2 | `test_foreign_expired_wrong_aud_wrong_iss_and_access_tokens_are_rejected_on_search` | PASS |
| 3 | `test_display_name_staff_does_not_grant_admin_for_registration` | PASS |
| 4 | `test_custom_role_admin_claim_does_not_grant_access_to_other_users_documents` | PASS |
| 5 | `test_copying_another_users_display_name_does_not_grant_their_file` | PASS |
| 6 | `test_login_fails_closed_when_cognito_is_unreachable` | PASS |
| 7 | `test_login_role_comes_only_from_cognito_groups` | PASS |
| 8 | `test_auth_users_requires_authentication` | PASS |
| 9 | `test_auth_me_rejects_unsigned_token` | PASS |
| 10 | `test_unhandled_error_returns_generic_500` | PASS |
| 11 | `test_authenticated_request_writes_no_identity_to_stdout_or_logs` | PASS |
| 12 | `test_any_token_defect_is_rejected_on_every_documents_and_search_route` | PASS |

Pytest summary line: `12 passed in 0.56s`
