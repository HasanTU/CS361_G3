# backend_v2 Auth Bypass Bugfix Design

## Overview

The three backend_v2 Lambdas (auth, documents, search) read the login token with base64 and trust whatever it says. Nothing checks the signature, issuer, audience or expiry, and API Gateway has no authorizer. On top of that unverified identity, the handlers guess admin status from text, match documents to users by display name, email and alias, and fall back to a hard-coded admin and a plaintext password compare when Cognito fails.

The fix replaces all of that with one verified identity:

1. A shared package `backend_v2/shared/auth/` verifies every Cognito ID token in code (RS256 against the user pool JWKS, plus issuer, audience, expiry and `token_use`) with PyJWT. It turns verified claims into a `Caller` object. Admin is decided in one place: `"ADMIN" in cognito:groups`.
2. Documents are authorized by the caller's Cognito `sub` only. `assigned_to` stores `sub` values. Recipient names typed in the frontend are resolved to a `sub` on the server, against the Cognito user pool, when a document is registered or forwarded.
3. The login use case only talks to Cognito. Every fallback (MongoDB, dev admin, `alg: none` token) is deleted. When Cognito cannot be reached, login fails closed with HTTP 503.
4. Handlers return a generic HTTP 500 body and log details server-side. No identity dumps.
5. A one-time script rewrites existing `assigned_to` values (names, emails, aliases) to `sub` values. It runs as a dry run unless `--apply` is passed, and it writes a backup that a `--restore` mode can put back.

The frontend does not change. It keeps sending display names and the Bearer header or cookie it sends today.

All paths in this document are relative to the project folder `CS361_G3/` unless they start with `.kiro/`.

## Glossary

- **Bug_Condition (C)**: A request or login for which the unfixed code grants an identity, role or document access that a verified Cognito identity would not grant, or leaks error or identity details. Defined formally in `isBugCondition` below.
- **Property (P)**: The required result for inputs in C: reject with the status and message in the "HTTP status and message table", authorize only by verified `sub` and `cognito:groups`, and never leak exception text or claims.
- **Preservation**: For inputs outside C (valid Cognito ID tokens, legitimate logins, legitimate recipients), the fixed code returns the same status codes, bodies, cookies and stored data as the unfixed code, apart from the `assigned_to` format change from names to `sub` values.
- **F / F'**: F is the unfixed code at commit `b52f483`. F' is the code after this fix.
- **Verified ID token**: A JWT that the user pool signed with RS256 using a key in its JWKS, with `iss` equal to the pool issuer URL, `aud` equal to `COGNITO_CLIENT_ID`, `token_use` equal to `"id"`, and `exp` in the future.
- **Caller**: The frozen dataclass `backend_v2.shared.auth.caller.Caller` built from verified claims: `sub`, `email`, `name`, `groups`, `faculty`, `claims`.
- **sub**: The Cognito `sub` claim, a UUID that never changes for a user. It is the only value used for document authorization.
- **Actor label**: `Caller.actor_label`, the verified `email` (or `sub` when email is missing). It is written to `uploaded_by` and `status_history[].changed_by`, which keeps the audit trail readable exactly as today.
- **Directory user**: `DirectoryUser(sub, email, name)`, one enabled user in the Cognito user pool, returned by `UserDirectoryPort.list_users()`.
- **Legacy identity value**: Any `assigned_to` or `receiver` string stored before the fix: a display name, an email, a username such as `user1`, a persona code such as `HEAD-1`, or an alias from `KNOWN_USER_ALIASES`.
- **`_parse_auth`**: The function duplicated in `backend_v2/functions/documents/handler.py` and `backend_v2/functions/search/handler.py` that decodes the token without verification, expands aliases and computes `is_admin` by heuristics. It is deleted.
- **`_decode_jwt_payload`**: The base64 claim decoder duplicated in the three handlers and `auth/use_cases.py`. It is deleted.
- **`Document.is_accessible_by`**: The method in `backend_v2/shared/domain/document.py` that decides read access. Today it matches any identity string against `assigned_to` and `receiver`, ignoring case.

## Bug Details

### Bug Condition

The bug manifests whenever a request reaches a backend_v2 Lambda, because the identity in the request is never verified. Seven code paths turn that into a concrete defect: token acceptance in `_parse_auth` and `/auth/me`, admin heuristics in `_parse_auth` and `AuthUseCases.login`, login fallbacks in `AuthUseCases.login`, the unauthenticated `/auth/users` route, name-based document matching in `Document.is_accessible_by` and `MongoDocumentRepository.find_by_assigned_user`, the catch-all `except Exception` in each handler, and the `[AUTH_DEBUG]` print.

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input = (event, storedDocuments, cognitoState)
         event         : API Gateway HTTP API v2 event (method, rawPath, headers, cookies, body)
         storedDocuments: documents in MongoDB, each with assigned_to[] and receiver
         cognitoState  : REACHABLE | UNREACHABLE | NOT_CONFIGURED, plus the user pool contents
  OUTPUT: boolean

  token  := extractToken(event)          // Bearer header, then event.cookies, then Cookie header
  route  := (event.method, event.rawPath)

  // C1: token accepted although it is not a verified ID token (1.1, 1.2, 1.3, 1.15)
  C1 := token != NULL
        AND route targets documents, search, or GET /api/v1/auth/me
        AND NOT isVerifiedIdToken(token)

  // C2: admin granted by something other than verified group membership (1.4, 1.5, 1.6, 1.7)
  claims := decodeWithoutVerification(token)
  C2 := token != NULL
        AND "ADMIN" NOT IN claims["cognito:groups"]
        AND ( anyIdentity(claims) contains "admin" or "staff" (case-insensitive)
              OR anyIdentity(claims) IN HARDCODED_ADMIN_IDENTITIES
              OR lower(claims["custom:role"] or claims["role"]) == "admin" )
     OR ( route == POST /api/v1/auth/login
          AND cognito returns a token whose groups lack "ADMIN"
          AND ( lower(email) startswith "admin" OR lower(email) IN ["admin1@tu.ac.th","admin2@tu.ac.th"]
                OR lower(claims["custom:role"] or claims["role"]) == "admin" ) )

  // C3: login succeeds without Cognito authenticating the credentials (1.8, 1.9, 1.3)
  C3 := route == POST /api/v1/auth/login
        AND cognitoState IN {UNREACHABLE, NOT_CONFIGURED}   // includes any non-credential boto error
        AND ( mongoUser(email).password_hash == password
              OR check_password_hash(mongoUser(email).password_hash, password)
              OR (email, password) == ("admin@kanna.ac.th", "admin1234") )

  // C4: user directory served without authentication (1.10)
  C4 := route == GET /api/v1/auth/users AND NOT isVerifiedIdToken(token)

  // C5: document access through a non-sub identity (1.11, 1.12)
  C5 := token != NULL AND NOT isAdminByGroup(claims)
        AND route IN {list, details, file, status, assign, search trie}
        AND EXISTS doc IN storedDocuments:
              claims["sub"] NOT IN doc.assigned_to
              AND EXISTS v IN expandAliases(anyIdentity(claims)):
                    lower(v) IN lower(doc.assigned_to) OR lower(v) == lower(doc.receiver)

  // C6: exception text returned to the client (1.13)
  C6 := handlerRaisesUnhandledException(event)

  // C7: identities written to logs (1.14)
  C7 := token != NULL AND route targets the documents Lambda

  RETURN C1 OR C2 OR C3 OR C4 OR C5 OR C6 OR C7
END FUNCTION

FUNCTION isVerifiedIdToken(token)
  header := unverifiedHeader(token)
  RETURN header.alg == "RS256"
         AND header.kid IN keysOf(JWKS(COGNITO_USER_POOL_ID))
         AND signatureValid(token, JWKS[header.kid])
         AND claims.iss == "https://cognito-idp.<region>.amazonaws.com/<COGNITO_USER_POOL_ID>"
         AND claims.aud == COGNITO_CLIENT_ID
         AND claims.token_use == "id"
         AND claims.exp > now - 10 seconds
         AND claims.sub is a non-empty string
END FUNCTION

anyIdentity(claims) := non-empty values of claims.email, claims.name,
                       claims["cognito:username"], claims.username, claims.sub
HARDCODED_ADMIN_IDENTITIES := ["admin1@tu.ac.th","admin2@tu.ac.th","kasidit","saowaluk",
                               "staff1@tu.ac.th","staff-1","ธารินี","คุณธารินี สุขสวัสดิ์"]
```

C7 holds only for the documents Lambda because the `[AUTH_DEBUG]` print exists only in `backend_v2/functions/documents/handler.py`. The search `_parse_auth` does not print.

### Examples

- **Forged admin token (C1, C2).** A client sends `Authorization: Bearer <header {"alg":"none"}>.<{"sub":"x","cognito:groups":["ADMIN"]}>.sig` to `GET /api/v1/documents`. F returns 200 with every document. F' returns 401 `{"error":{"message":"Invalid authentication token"}}`.
- **Expired or foreign token (C1).** A real Cognito ID token from another user pool, or one with `exp` an hour ago, is sent to `GET /api/v1/search/trie`. F returns 200. F' returns 401 "Invalid authentication token".
- **Server-minted token (C1, C3).** Cognito is unreachable and the client logs in as `admin@kanna.ac.th` / `admin1234`. F returns 200, an admin user object and a token ending in `.mock_signature`, and the documents Lambda accepts that token. F' returns 503 `{"error":{"message":"Authentication service is unavailable"}}` and never mints a token.
- **Display-name escalation (C2).** A regular user in group `USER` whose Cognito `name` is "Staff Helper" calls `POST /api/v1/documents`. F treats them as admin and registers the document (201). F' returns 403 "Admin privilege required to register documents".
- **Name collision (C5).** `fac_a@tu.ac.th` changes their Cognito `name` to "อ.ดร.ณัฐวุฒิ ศรีวัฒน์" (FAC-B's name) and calls `GET /api/v1/documents/<id>/file` for a document assigned to FAC-B. F serves the file. F' returns 403 "Access denied: You are not assigned to this document", because `fac_a`'s `sub` is not in `assigned_to`.
- **Alias expansion (C5).** A token with `cognito:username` `user1` expands to "Prapaporn" and "User One", and F grants every document whose `receiver` is "User One". F' ignores usernames and aliases.
- **Unauthenticated directory (C4).** `GET /api/v1/auth/users` with no token. F returns 200 and a name list that includes hard-coded names. F' returns 401 "Authentication is required".
- **Error leak (C6).** A Mongo timeout inside `list_documents`. F returns 500 `Internal server error: ServerSelectionTimeoutError ...cluster0.xxx.mongodb.net...`. F' returns 500 `{"error":{"message":"Internal server error"}}` and logs the traceback.
- **Edge case, legitimate admin.** `staff1@tu.ac.th` is in Cognito group `ADMIN`. Both F and F' let them list all documents. This input is outside C and must behave the same.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Login with correct Cognito credentials returns 200 with `{"user": {"id","email","name","role","faculty"}, "token": <IdToken>}` and sets the `auth_token` cookie with the same attributes: `Path=/; HttpOnly; Secure; SameSite=None; Max-Age=86400`, in both the `Set-Cookie` header and the `cookies` array (3.1).
- Wrong password or unknown email returns 401 "Invalid email or password" (3.2). An empty email or password returns 400 "Email and password are required" (3.3).
- Logout returns 200 `{"message":"Logged out successfully"}` and clears the cookie, without requiring a token (3.4).
- Token lookup order stays: `Authorization: Bearer`, then `event["cookies"]`, then the `Cookie` header (3.5). No token returns 401 "Authentication is required" (3.6).
- Users in the Cognito `ADMIN` group keep full access: list all, register, assign and forward any document, change any status, view and download any file, and get the full search trie (3.7).
- A verified non-admin calling `POST /api/v1/documents` gets 403 "Admin privilege required to register documents" (3.8).
- An assigned non-admin can list, view, download, change status along `ALLOWED_STATUS_TRANSITIONS`, forward, and see the document in the trie (3.9). A non-assigned non-admin gets 403 with the existing messages and does not see the document in lists or the trie (3.10).
- Assign and forward still replace `assigned_to`, move the status to ASSIGNED (from RECEIVED) or FORWARDED, and append one `status_history` entry (3.11).
- Unknown document ids return 404 "Document was not found: <id>" (3.12). Invalid transitions, bad JSON and bad multipart forms return 400 with their current messages (3.13).
- File responses keep `Content-Disposition: attachment` with `download=true` and `inline` otherwise, with the stored file name, base64 body and `isBase64Encoded: true` (3.14).
- OPTIONS returns 204 with the same CORS headers as today, before any authentication (3.15).
- `uploaded_by` and `status_history[].changed_by` keep holding the caller's email, which the lecturer view shows as "ผู้ดำเนินการ".
- `receiver` stays a display-name string. The search trie keeps indexing `title`, `sender` and `receiver`.
- `set_use_cases()` keeps working in all three handlers, and handlers run offline in tests (3.17).

**Scope:**
All inputs where `isBugCondition` is false must be unaffected. This includes:
- Requests that carry a verified Cognito ID token, for both `ADMIN` and `USER` group members whose email and name do not trigger the old heuristics.
- Document operations where the caller's `sub` is in `assigned_to`, or where the caller is in `ADMIN`.
- Logins where Cognito is reachable, including wrong-password logins.
- OPTIONS preflights, logout, and requests with no token.
- Request validation errors (bad JSON, missing form fields, invalid status transitions, unknown ids).

**Intentional differences that are not regressions:**
- `assigned_to` holds `sub` values instead of names. The assign response field `assigned_to` therefore returns `sub` values. The frontend does not read that field (checked: no `assigned_to` reads in `frontend/`).
- Access no longer matches the `receiver` field. Under F, the original receiver of a forwarded document kept access through `receiver` even after `assigned_to` was replaced. Under F', only the current `assigned_to` members have access, which is what 2.11 and 3.11 require. The separate forwarding spec will add non-overwriting delivery records.
- `GET /api/v1/auth/users` returns `{"users": [{"id": "<sub>", "name": "<name>"}]}` instead of `{"users": [{"name": ...}]}`. The frontend reads only `user.name`, so it keeps working.
- A recipient value that matches no Cognito user, or matches more than one user by name, now returns 400 instead of being stored as free text.

## Hypothesized Root Cause

The causes below were confirmed by reading the code at commit `b52f483`, so they are findings rather than guesses. The exploratory tests in the Testing Strategy confirm them by execution.

1. **No token verification anywhere.** `_decode_jwt_payload` is copied into `auth/handler.py`, `auth/use_cases.py`, `documents/handler.py` and `search/handler.py`. It splits the token on `.` and base64-decodes the middle part. `aws_deploy/deploy_v2.py` `configure_apigw_v2` creates routes with no `AuthorizerId`. Nothing checks `alg`, signature, `iss`, `aud`, `exp` or `token_use`.

2. **The server mints unsigned tokens.** `AuthUseCases._synthesize_test_token` builds `{"alg":"none"}` tokens with the signature `mock_signature`. It is called from the MongoDB fallback and the dev admin branch of `login`. Because of cause 1, those tokens work in production.

3. **Fail-open login.** In `AuthUseCases.login`, any exception from `initiate_auth` whose text does not contain `NotAuthorizedException` or `UserNotFoundException` falls through to the MongoDB lookup. That lookup accepts `pw_hash == password`, and after it the hard-coded `admin@kanna.ac.th` / `admin1234` branch runs. `_get_cognito_client` also swallows boto3 construction errors and returns `None`, which skips Cognito entirely.

4. **Role decided by text, in three copies.** `_parse_auth` (documents and search) sets `is_admin` when any identity string contains "admin" or "staff", matches a hard-coded list, or `custom:role`/`role` equals "admin". The identity list includes the `name` claim, which users can change. `login` uses a different rule (`email.startswith("admin")` and a different list). So the login role and the enforced role can disagree.

5. **Authorization by mutable strings.** `_parse_auth` builds an identity list from email, name, username and sub, then expands it with `KNOWN_USER_ALIASES`. `Document.is_accessible_by` lower-cases everything and matches against `assigned_to` and `receiver`. `MongoDocumentRepository.find_by_assigned_user` runs case-insensitive regex matches on both fields. The frontend stores display names in `receiver` and in `recipients`, so `assigned_to` holds names.

6. **Unauthenticated directory.** The `GET /api/v1/auth/users` branch in `auth/handler.py` calls `use_cases.list_users()` without reading a token. `list_users` merges MongoDB names with a hard-coded set.

7. **Leaky error handling and logging.** Each handler ends with `except Exception as err: return _error_response(500, f"Internal server error: {str(err)}")`. The documents and search handlers also return `f"Invalid authentication token: {err}"`. `documents/handler.py` prints `[AUTH_DEBUG] identities=..., groups=..., role=...` on every request.

## Correctness Properties

Property 1: Bug Condition - Only verified Cognito identities and groups authorize requests

_For any_ input where the bug condition holds (isBugCondition returns true), the fixed handlers SHALL: reject tokens that are not verified ID tokens with HTTP 401 "Invalid authentication token" before any use case runs; grant admin only when the verified `cognito:groups` contains `ADMIN`; refuse logins that Cognito did not authenticate (401 for bad credentials, 503 "Authentication service is unavailable" when Cognito cannot be reached) without minting tokens; reject `GET /api/v1/auth/users` and `GET /api/v1/auth/me` without a verified token with 401; give a non-admin access to a document only if their verified `sub` is in `assigned_to`, otherwise 403 for details, file, status and assign and omission from list and trie; return HTTP 500 `{"error":{"message":"Internal server error"}}` with no exception text for unhandled errors; and write no identities, groups or claims to stdout or logs.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12, 2.13, 2.14, 2.15**

Property 2: Preservation - Legitimate requests behave as before

_For any_ input where the bug condition does NOT hold (isBugCondition returns false), the fixed handlers SHALL produce the same HTTP status code, response body, cookies and persisted document changes as the original handlers, preserving login and logout responses, the 401 "Authentication is required" response, full admin access for `ADMIN` group members, the 403 responses for non-admin registration and non-assigned access, assigned-recipient access and workflow transitions, assign and forward replacement semantics, 404 and 400 responses, file disposition, OPTIONS preflight, and offline handler execution through `set_use_cases()`. The comparison treats `assigned_to` entries as equal when they identify the same Cognito user (a `sub` in F' and the `sub` that the legacy value resolves to in F).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13, 3.14, 3.15, 3.17**

Property 3: Role consistency - One role rule everywhere

_For any_ verified claims set, the role in the login response, the `is_admin` value used by the documents and search handlers, and the role derived for `/api/v1/auth/me` SHALL all equal `"admin"` exactly when `"ADMIN"` is an element of `cognito:groups`, and SHALL NOT depend on `email`, `name`, `cognito:username`, `username`, `sub`, `custom:role` or `role`.

**Validates: Requirements 2.4, 2.5, 2.6, 2.7**

Property 4: Migration - Legacy recipients keep access, and only them

_For any_ stored document and any Cognito user pool, the migration plan SHALL map each legacy `assigned_to` value (or `receiver` when `assigned_to` is empty) to the `sub` of the user that value identified before the fix (by exact `sub`, by email ignoring case, by the legacy alias table, or by a display name that matches exactly one user), SHALL leave documents with any unresolved value unchanged and report them, SHALL be idempotent (running it on its own output plans no changes), and SHALL write nothing in dry-run mode.

**Validates: Requirements 3.16, 2.11, 2.12**

## Fix Implementation

### Key Decisions

**D1. Verify tokens in code with PyJWT against the Cognito JWKS. Do not use an API Gateway JWT authorizer.**

Reasons, from the current code:
- Requirement 3.5 keeps cookie authentication (`event["cookies"]` and the `Cookie` header). An HTTP API JWT authorizer reads the token from one identity source such as `$request.header.Authorization` and cannot pull `auth_token` out of a cookie string. Requests that rely on the cookie would be rejected.
- Requirement 3.6 keeps the body `{"error":{"message":"Authentication is required"}}`. A JWT authorizer answers with its own `{"message":"Unauthorized"}` before the Lambda runs.
- `/api/v1/auth/login` and `/api/v1/auth/logout` must stay public, but `deploy_v2.py` routes `ANY /api/v1/auth/{proxy+}` to one integration. An authorizer would require splitting routes per path in `configure_apigw_v2`, and updating existing routes that `create_route` currently skips with `except ClientError: pass`.
- Requirement 3.17 requires offline tests. In-code verification is fully testable with a locally generated RSA key. An authorizer cannot be exercised offline, and handlers would still need code to read `requestContext.authorizer.jwt.claims`.
- All role logic moves into one shared module either way, so in-code verification adds no duplication.

A JWT authorizer can be added later for header-only routes as defense in depth. It is out of scope here.

**D2. Library: `pyjwt[crypto]>=2.10.1,<3`.** PyJWT is maintained, has `PyJWKClient` for JWKS fetching with caching, and supports explicit `algorithms=["RS256"]`. The `crypto` extra pulls in `cryptography` for RSA. 2.10.1 is the minimum because 2.10.0 had an issuer-check bug (CVE-2024-53861).

**D3. Accept Cognito ID tokens only (`token_use == "id"`).** `login` already prefers `IdToken`. ID tokens carry `aud`, `email`, `name`, `cognito:groups` and `custom:faculty`. Access tokens have no `aud` and no `email`. The `AccessToken` fallback in `login` is removed.

**D4. User directory: Cognito `ListUsers` at runtime, behind a new port.** The documents and auth Lambdas need to map names to `sub` values and list users. The Cognito user pool is the only store that has `sub` values. MongoDB `users` has none. A new `UserDirectoryPort` with a `CognitoUserDirectory` adapter follows the existing ports and adapters layout. Results are cached for 300 seconds per warm container.

Deployment risk: `LabRole` must allow `cognito-idp:ListUsers`. This is **not verified**. The deployment smoke test (task list, final checkpoint) checks it. If CloudWatch shows `AccessDeniedException` for `ListUsers`, the executor must stop and report. Do not add a fallback without the user's approval.

**D5. Recipients: the server resolves names to `sub` values. The frontend does not change.** `frontend/html/staff_upload.html` posts `receiver=<display name>`, and `frontend/html/lecturer_view.html` posts `recipients: ["<display name>"]` from a hard-coded list. The documents use cases resolve each value by exact `sub`, then email ignoring case, then a display name that matches exactly one Cognito user. Anything else is a 400. Resolution only picks whose `sub` goes into `assigned_to`. Authorization never looks at names.

Residual risk: Cognito users can change their own `name` attribute through the Cognito API. A user who copies another user's name makes that name ambiguous, so sends to that name fail with 400 until an admin fixes the attribute. That is a denial of service, not a data leak. Restricting the app client's write attributes or sending `sub` values from the frontend would close it. Both are out of scope.

**D6. Packaging: one shared zip, so a package under `backend_v2/shared/` reaches every Lambda.** `aws_deploy/deploy_v2.py` `build_v2_package()` copies the whole `backend_v2/` tree into one zip, and all three functions in `MICRO_FUNCTIONS` use that zip. `aws_deploy/helpers/lambda_builder.py` builds the v1 Flask package only and must not be changed. New dependencies go in `V2_DEPENDENCIES` in `deploy_v2.py`.

**D7. Build target must match the Lambda runtime.** The functions use `Runtime="python3.11"`, which runs on Amazon Linux 2 (glibc 2.26). `build_v2_package()` passes `--python-platform linux`, which uv documents as `x86_64-unknown-linux-gnu`, equivalent to `x86_64-manylinux_2_28` ([uv CLI reference](https://docs.astral.sh/uv/reference/cli/)). A `manylinux_2_28` wheel of `cryptography` fails to import on glibc 2.26. The build must use `--python-platform x86_64-manylinux2014` (glibc 2.17) and `--only-binary :all:`, so the build fails loudly instead of compiling for the build machine.

**D8. Tests: pytest plus Hypothesis, offline, with a locally generated RSA key.** There are no tests in the repository today. `.gitignore` and `.pytest_cache/` show pytest is the expected runner. Hypothesis provides the property-based tests.

### Changes Required

#### New package `backend_v2/shared/auth/`

The folder has no `__init__.py` siblings above it (`backend_v2/` and `backend_v2/shared/` are namespace packages today). Keep it that way. Only `backend_v2/shared/auth/__init__.py` is added.

**File**: `backend_v2/shared/auth/errors.py`

```python
AUTH_REQUIRED_MESSAGE = "Authentication is required"
INVALID_TOKEN_MESSAGE = "Invalid authentication token"
SERVICE_UNAVAILABLE_MESSAGE = "Authentication service is unavailable"
INTERNAL_ERROR_MESSAGE = "Internal server error"

class AuthenticationError(PermissionError):
    """Missing or invalid credentials. Handlers map it to HTTP 401."""

class AuthServiceUnavailableError(Exception):
    """Cognito or its JWKS endpoint cannot be reached. Handlers map it to HTTP 503."""
```

`AuthenticationError` subclasses `PermissionError` so code that already catches `PermissionError` stays safe. Every handler must catch `AuthenticationError` before `PermissionError`.

**File**: `backend_v2/shared/auth/caller.py`

```python
ADMIN_GROUP = "ADMIN"

@dataclass(frozen=True)
class Caller:
    sub: str
    email: str = ""
    name: str = ""
    groups: tuple[str, ...] = ()
    faculty: str = ""
    claims: dict[str, Any] = field(default_factory=dict, compare=False, repr=False, hash=False)

    @property
    def is_admin(self) -> bool:        # ADMIN_GROUP in self.groups (exact, case-sensitive)
    @property
    def role(self) -> str:             # "admin" if self.is_admin else "user"
    @property
    def actor_label(self) -> str:      # self.email or self.sub

def caller_from_claims(claims: Mapping[str, Any]) -> Caller:
    # sub: str(claims["sub"]).strip(); if missing or empty -> raise AuthenticationError(INVALID_TOKEN_MESSAGE)
    # groups: tuple(str(g) for g in claims["cognito:groups"]) if it is a list, else ()
    # email: str(claims.get("email") or "").strip()
    # name: str(claims.get("name") or "").strip()
    # faculty: str(claims.get("custom:faculty") or "").strip()
    # claims: dict(claims)

def require_admin(caller: Caller, message: str) -> None:
    # if not caller.is_admin: raise PermissionError(message)
```

`caller_from_claims` must not read `custom:role`, `role`, `cognito:username` or `username`. `repr=False` on `claims` keeps claims out of accidental log lines.

**File**: `backend_v2/shared/auth/token_verifier.py`

```python
class CognitoTokenVerifier:
    ALGORITHM = "RS256"

    def __init__(self, user_pool_id: str, client_id: str,
                 signing_key_provider: Any = None, leeway_seconds: int = 10):
        # user_pool_id must look like "<region>_<id>"; else raise RuntimeError("COGNITO_USER_POOL_ID is missing or malformed")
        # client_id must be non-empty; else raise RuntimeError("COGNITO_CLIENT_ID is missing")
        # region = user_pool_id.split("_", 1)[0]
        # self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        # self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        # self._keys = signing_key_provider or jwt.PyJWKClient(
        #     self.jwks_url, cache_jwk_set=True, lifespan=3600, timeout=5)

    @classmethod
    def from_env(cls) -> "CognitoTokenVerifier":
        # cls(os.getenv("COGNITO_USER_POOL_ID", ""), os.getenv("COGNITO_CLIENT_ID", ""))

    def verify(self, token: str) -> dict[str, Any]:
        ...
```

`verify` steps, in order. Every failure raises `AuthenticationError(INVALID_TOKEN_MESSAGE)` with `from None`, so the message never carries library detail, unless stated otherwise:

1. Empty or non-string token: raise `AuthenticationError(AUTH_REQUIRED_MESSAGE)`.
2. `header = jwt.get_unverified_header(token)`. Catch `jwt.PyJWTError`.
3. If `header.get("alg") != "RS256"` or `header.get("kid")` is falsy, reject. This rejects `alg: none` before any network call.
4. `signing_key = self._keys.get_signing_key_from_jwt(token)`. Catch `jwt.PyJWKClientConnectionError` first and raise `AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE)`. Then catch `jwt.PyJWTError` (unknown `kid`) and reject.
5. `claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=self._client_id, issuer=self.issuer, leeway=self._leeway, options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]})`. Catch `jwt.PyJWTError`.
6. If `claims.get("token_use") != "id"`, reject.
7. Return `claims`.

Configuration errors are `RuntimeError` on purpose. A `ValueError` would be mapped to HTTP 400 by the handlers' `except ValueError` and would show the configuration message to the client.

`signing_key_provider` is any object with `get_signing_key_from_jwt(token) -> object with .key`. Production uses `jwt.PyJWKClient`. Tests pass a static provider (see Testing Strategy).

**File**: `backend_v2/shared/auth/request_auth.py`

```python
_verifier: Optional[CognitoTokenVerifier] = None

def get_token_verifier() -> CognitoTokenVerifier:   # lazily CognitoTokenVerifier.from_env(), cached in _verifier
def set_token_verifier(verifier: Optional[CognitoTokenVerifier]) -> None:   # tests; None resets
def extract_token(event: dict[str, Any]) -> Optional[str]:
    # Same logic and order as today's _extract_cookie_token:
    # 1. headers "authorization"/"Authorization" starting with "Bearer " -> stripped remainder (return None if empty)
    # 2. event["cookies"] item starting with "auth_token="
    # 3. headers "cookie"/"Cookie" part starting with "auth_token="
def authenticate(event: dict[str, Any], verifier: Optional[CognitoTokenVerifier] = None) -> Caller:
    # token = extract_token(event); if not token: raise AuthenticationError(AUTH_REQUIRED_MESSAGE)
    # claims = (verifier or get_token_verifier()).verify(token)
    # return caller_from_claims(claims)
```

The missing-token check runs before the verifier is built, so a request with no token returns 401 even when the Cognito variables are unset.

**File**: `backend_v2/shared/auth/__init__.py`

Re-exports: `AUTH_REQUIRED_MESSAGE`, `INVALID_TOKEN_MESSAGE`, `SERVICE_UNAVAILABLE_MESSAGE`, `INTERNAL_ERROR_MESSAGE`, `AuthenticationError`, `AuthServiceUnavailableError`, `ADMIN_GROUP`, `Caller`, `caller_from_claims`, `require_admin`, `CognitoTokenVerifier`, `extract_token`, `authenticate`, `get_token_verifier`, `set_token_verifier`. List them in `__all__`.

#### User directory (new domain type, port and adapter)

**File**: `backend_v2/shared/domain/directory_user.py` (pure Python, no third-party imports)

```python
@dataclass(frozen=True)
class DirectoryUser:
    sub: str
    email: str
    name: str

def resolve_recipient(value: Any, users: Sequence[DirectoryUser]) -> DirectoryUser:
    # not a str -> ValueError("Recipient must be a string")
    # cleaned = value.strip(); empty -> ValueError("Recipient is required")
    # 1. user.sub == cleaned                         -> that user
    # 2. user.email.casefold() == cleaned.casefold() -> that user
    # 3. users whose name.strip().casefold() == cleaned.casefold():
    #      exactly one -> that user
    #      more than one -> ValueError(f"Recipient name is ambiguous: {cleaned}")
    # 4. otherwise -> ValueError(f"Unknown recipient: {cleaned}")

def resolve_recipients(values: Sequence[Any], users: Sequence[DirectoryUser]) -> list[DirectoryUser]:
    # empty -> ValueError("Must assign at least one recipient user ID")  (same text as Document.assign_recipients)
    # resolve each in order; drop later duplicates by sub; return list
```

Add `DirectoryUser`, `resolve_recipient` and `resolve_recipients` to `backend_v2/shared/domain/__init__.py` and its `__all__`.

**File**: `backend_v2/shared/ports/user_directory.py`

```python
class UserDirectoryPort(ABC):
    @abstractmethod
    def list_users(self) -> list[DirectoryUser]:
        """Return enabled users of the identity provider, sorted by (name.casefold(), sub)."""
```

Add `UserDirectoryPort` to `backend_v2/shared/ports/__init__.py` and its `__all__`.

**File**: `backend_v2/shared/adapters/cognito_user_directory.py`

```python
class CognitoUserDirectory(UserDirectoryPort):
    def __init__(self, user_pool_id: Optional[str] = None, cognito_client: Any = None,
                 cache_ttl_seconds: float = 300.0, clock: Callable[[], float] = time.monotonic):
        # self._pool_id = user_pool_id or os.getenv("COGNITO_USER_POOL_ID", "")

    def list_users(self) -> list[DirectoryUser]:
        # if no pool id -> RuntimeError("COGNITO_USER_POOL_ID is missing")
        # return a copy of the cache if it is younger than cache_ttl_seconds
        # client = self._client or boto3.client("cognito-idp", region_name=self._pool_id.split("_", 1)[0])  (import boto3 inside the method)
        # for page in client.get_paginator("list_users").paginate(UserPoolId=self._pool_id):
        #   for u in page.get("Users", []):
        #     skip if u.get("Enabled") is False
        #     attrs = {a["Name"]: a["Value"] for a in u.get("Attributes", [])}
        #     skip if not attrs.get("sub")
        #     email = attrs.get("email", ""); name = attrs.get("name") or email.split("@")[0] or attrs["sub"]
        # sort by (name.casefold(), sub), store in cache with the clock time, return a copy
```

Boto errors are not caught here. They reach the handler's catch-all and become a generic 500. Add `CognitoUserDirectory` to `backend_v2/shared/adapters/__init__.py` and its `__all__`.

#### Domain and repository

**File**: `backend_v2/shared/domain/document.py`

**Function**: `Document.is_accessible_by`

Replace with:
```python
def is_accessible_by(self, user_sub: str, is_admin: bool = False) -> bool:
    if is_admin:
        return True
    if not isinstance(user_sub, str) or not user_sub.strip():
        return False
    return user_sub.strip() in self.assigned_to
```
Exact, case-sensitive comparison. No `receiver` match. No list input.

**File**: `backend_v2/shared/adapters/mongo_document_repository.py`

**Function**: `MongoDocumentRepository.find_by_assigned_user`

Replace with: return `[]` when `user_sub` is not a non-empty string. Otherwise run `self.collection.find({"assigned_to": user_sub.strip()})` and convert records with the same `_to_domain` and skip-on-error loop as today. Remove the `import re`, the regex conditions and the `receiver` conditions. Change the parameter name to `user_sub: str`.

**File**: `backend_v2/shared/ports/document_repository.py`

Rename the `find_by_assigned_user` parameter to `user_sub: str` and set the docstring to "Retrieve all documents whose assigned_to list contains the given Cognito sub (exact match)."

#### Documents Lambda

**File**: `backend_v2/functions/documents/use_cases.py`

**Class**: `DocumentUseCases`

```python
def __init__(self, repository: DocumentRepositoryPort, storage: FileStoragePort,
             user_directory: Optional[UserDirectoryPort] = None)

def _directory_users(self) -> list[DirectoryUser]:
    # if self._directory is None: raise RuntimeError("User directory is not configured")

def register_document(self, title, document_date, sender, receiver, file_name, file_bytes,
                      content_type="application/octet-stream", uploaded_by="system",
                      document_type_id=None) -> Document
    # 1. recipient = resolve_recipient(receiver, self._directory_users())   # BEFORE save_file, so a bad recipient leaves no file
    # 2. save_file as today
    # 3. Document(receiver=recipient.name, uploaded_by=uploaded_by, ...)
    # 4. document.assign_recipients([recipient.sub], assigned_by=uploaded_by, note="Assigned upon registration")
    # 5. create; keep the existing rollback that deletes the file on any exception after save_file
    # The recipient_ids parameter is removed.

def list_documents(self, caller_sub: str, is_admin: bool = False) -> list[Document]
def get_document_details(self, document_id: str, caller_sub: str, is_admin: bool = False) -> Document
def get_document_file(self, document_id: str, caller_sub: str, is_admin: bool = False) -> Tuple[Path, Document]

def assign_document(self, document_id: str, recipients: list[str], caller_sub: str, actor: str,
                    is_admin: bool = False, note: str = "") -> Document
    # 1. get_by_id -> LookupError(f"Document was not found: {document_id}")  (404 before 403, as today)
    # 2. if not (is_admin or document.is_accessible_by(caller_sub)): PermissionError("Permission required to assign or forward this document")
    # 3. users = resolve_recipients(recipients, self._directory_users())   # after the permission check, so the directory is not probed by outsiders
    # 4. document.assign_recipients([u.sub for u in users], assigned_by=actor, note=note)
    # 5. repo.update with status, assigned_to, status_history (same payload as today)

def update_document_status(self, document_id: str, new_status: str, caller_sub: str, actor: str,
                           is_admin: bool = False, note: str = "") -> Document
    # same flow as today, with is_accessible_by(caller_sub, is_admin) and changed_by=actor
```

Keep the existing 403 messages exactly: "Access denied: You are not assigned to this document" and "Access denied: You cannot update an unassigned document".

**File**: `backend_v2/functions/documents/handler.py`

- Delete `_extract_cookie_token`, `_decode_jwt_payload`, `KNOWN_USER_ALIASES`, `_parse_auth` and the `[AUTH_DEBUG]` print. Keep `import base64` (multipart and file responses use it). Remove `Tuple` from imports only if unused.
- `get_use_cases()` builds `DocumentUseCases(repository=MongoDocumentRepository(), storage=S3FileStorageAdapter(), user_directory=CognitoUserDirectory())`.
- Add `logger = logging.getLogger(__name__)`.
- In `lambda_handler`, keep the OPTIONS branch first and unchanged. Move `use_cases = get_use_cases()` inside the `try`. Then `caller = authenticate(event)`.
- Route changes:
  - list: `use_cases.list_documents(caller_sub=caller.sub, is_admin=caller.is_admin)`.
  - register: `require_admin(caller, "Admin privilege required to register documents")`, then the existing content-type, file and required-field checks, then `register_document(..., receiver=form_fields["receiver"], uploaded_by=caller.actor_label, document_type_id=...)`.
  - assign: `recipients = body.get("recipients", [])`; if it is not a list or any item is not a `str`, raise `ValueError("recipients must be a list of strings")`. Call `assign_document(document_id, recipients, caller_sub=caller.sub, actor=caller.actor_label, is_admin=caller.is_admin, note=body.get("note", ""))`.
  - status: `update_document_status(document_id, new_status, caller_sub=caller.sub, actor=caller.actor_label, is_admin=caller.is_admin, note=...)`.
  - file and details: pass `caller_sub=caller.sub, is_admin=caller.is_admin`.
- Exception mapping (order matters):
```python
except AuthenticationError as err:        return _error_response(401, str(err))
except AuthServiceUnavailableError:       return _error_response(503, SERVICE_UNAVAILABLE_MESSAGE)
except PermissionError as err:            return _error_response(403, str(err))
except LookupError as err:                return _error_response(404, str(err))
except ValueError as err:                 return _error_response(400, str(err))
except Exception:
    logger.exception("Unhandled error in documents handler")
    return _error_response(500, INTERNAL_ERROR_MESSAGE)
```

**File**: `backend_v2/functions/search/use_cases.py`

`SearchUseCases.get_search_trie(self, caller_sub: str, is_admin: bool = False)`. Same body, passing `caller_sub` to `find_by_assigned_user`.

**File**: `backend_v2/functions/search/handler.py`

- Delete `_extract_cookie_token`, `_decode_jwt_payload`, `KNOWN_USER_ALIASES`, `_parse_auth`, and the `base64` import (no other use).
- `caller = authenticate(event)`, then `get_search_trie(caller_sub=caller.sub, is_admin=caller.is_admin)`.
- Same exception mapping as the documents handler, without the `LookupError` and `ValueError` branches (the search handler has none today), and with `logger.exception("Unhandled error in search handler")`.

#### Auth Lambda

**File**: `backend_v2/functions/auth/use_cases.py`

Delete `_decode_jwt_payload`, `_get_user_collection`, the MongoDB fallback, the dev admin branch, `_synthesize_test_token`, the hard-coded name set and the `import base64` / `import json` lines if unused.

```python
INVALID_CREDENTIAL_CODES = frozenset({
    "NotAuthorizedException", "UserNotFoundException",
    "UserNotConfirmedException", "PasswordResetRequiredException",
})

class AuthUseCases:
    def __init__(self, cognito_client: Any = None, cognito_client_id: Optional[str] = None,
                 token_verifier: Optional[CognitoTokenVerifier] = None,
                 user_directory: Optional[UserDirectoryPort] = None)

    def _get_cognito_client(self) -> Any:
        # lazily boto3.client("cognito-idp", region_name=os.getenv("AWS_REGION", "us-east-1"))
        # do NOT swallow exceptions any more; wrap them: raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE) from None

    def login(self, email: str, password: str) -> Tuple[dict[str, Any], str]:
        # 1. clean_email = (email or "").strip().lower(); if not clean_email or not password: ValueError("Email and password are required")
        # 2. if not self._client_id: logger.error("COGNITO_CLIENT_ID is not configured"); raise AuthServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE)
        # 3. resp = cognito.initiate_auth(AuthFlow="USER_PASSWORD_AUTH", ClientId=self._client_id,
        #                                 AuthParameters={"USERNAME": clean_email, "PASSWORD": password})
        #    except botocore.exceptions.ClientError as err:
        #        code = err.response.get("Error", {}).get("Code", "")
        #        if code in INVALID_CREDENTIAL_CODES: raise PermissionError("Invalid email or password") from None
        #        logger.warning("Cognito initiate_auth failed with code %s", code); raise AuthServiceUnavailableError(...) from None
        #    except botocore.exceptions.BotoCoreError as err:
        #        logger.warning("Cognito initiate_auth failed: %s", type(err).__name__); raise AuthServiceUnavailableError(...) from None
        # 4. id_token = (resp.get("AuthenticationResult") or {}).get("IdToken")
        #    if not id_token (for example a ChallengeName response): raise PermissionError("Invalid email or password")
        # 5. claims = (self._verifier or get_token_verifier()).verify(id_token)
        #    except AuthenticationError: logger.error("Cognito returned a token that failed verification"); raise AuthServiceUnavailableError(...) from None
        # 6. caller = caller_from_claims(claims)
        # 7. user = {"id": caller.sub, "email": caller.email or clean_email,
        #            "name": caller.name or str(claims.get("cognito:username") or "") or clean_email.split("@")[0],
        #            "role": caller.role, "faculty": caller.faculty}
        # 8. return user, id_token

    def list_users(self) -> list[dict[str, str]]:
        # [{"id": u.sub, "name": u.name} for u in self._directory.list_users()]
        # if self._directory is None: raise RuntimeError("User directory is not configured")
```

Never log the email, password, token or claims. Log only Cognito error codes and exception class names.

**File**: `backend_v2/functions/auth/handler.py`

- Delete `_extract_cookie_token` and `_decode_jwt_payload`. Remove `import base64`.
- `get_use_cases()` builds `AuthUseCases(user_directory=CognitoUserDirectory())`.
- Login and logout branches: unchanged (same cookie strings, same 200 bodies).
- `GET /api/v1/auth/users`: `authenticate(event)` first (any authenticated user, admin or not, because lecturers forward documents), then `_json_response(200, {"users": use_cases.list_users()})`.
- `GET /api/v1/auth/me`: `caller = authenticate(event)`, then `_json_response(200, {"user": caller.claims})`.
- Exception mapping (order matters):
```python
except AuthenticationError as err:        return _error_response(401, str(err))
except AuthServiceUnavailableError:       return _error_response(503, SERVICE_UNAVAILABLE_MESSAGE)
except PermissionError as err:            return _error_response(401, str(err))   # login: "Invalid email or password"
except ValueError as err:                 return _error_response(400, str(err))
except Exception:
    logger.exception("Unhandled error in auth handler")
    return _error_response(500, INTERNAL_ERROR_MESSAGE)
```

#### HTTP status and message table

All error bodies use the existing envelope `{"error": {"message": "<message>"}}`.

| Situation | Lambda | Status | Message |
|---|---|---|---|
| No token on an authenticated route | all | 401 | `Authentication is required` |
| Token fails any verification step | all | 401 | `Invalid authentication token` |
| JWKS endpoint or Cognito unreachable, or `COGNITO_CLIENT_ID` unset at login | all | 503 | `Authentication service is unavailable` |
| Wrong password, unknown, unconfirmed or reset-required user, or no `IdToken` | auth | 401 | `Invalid email or password` |
| Empty email or password | auth | 400 | `Email and password are required` |
| Non-admin registers a document | documents | 403 | `Admin privilege required to register documents` |
| Non-assigned non-admin views details or file | documents | 403 | `Access denied: You are not assigned to this document` |
| Non-assigned non-admin changes status | documents | 403 | `Access denied: You cannot update an unassigned document` |
| Non-assigned non-admin assigns or forwards | documents | 403 | `Permission required to assign or forward this document` |
| Unknown document id | documents | 404 | `Document was not found: <id>` |
| Unknown route | all | 404 | `Route not found: <METHOD> <path>` (unchanged) |
| Recipient empty, not a string, unknown or ambiguous | documents | 400 | `Recipient is required`, `Recipient must be a string`, `Unknown recipient: <value>`, `Recipient name is ambiguous: <value>` |
| `recipients` not a list of strings | documents | 400 | `recipients must be a list of strings` |
| Empty `recipients` list | documents | 400 | `Must assign at least one recipient user ID` |
| Existing validation errors (bad JSON, multipart, fields, transitions) | documents | 400 | unchanged |
| Missing Cognito configuration in the verifier or directory, or any unhandled exception | all | 500 | `Internal server error` |

#### Deployment

**File**: `aws_deploy/deploy_v2.py`

1. Append `"pyjwt[crypto]>=2.10.1,<3"` to `V2_DEPENDENCIES`. Leave the other entries as they are.
2. In `build_v2_package()`, change the uv command from `"--python-platform", "linux"` to `"--python-platform", "x86_64-manylinux2014"` and add `"--only-binary", ":all:"` (see D7).
3. In `main()`, after `get_aws_session()` (which loads `.env`), stop the deployment when `COGNITO_USER_POOL_ID` or `COGNITO_CLIENT_ID` is empty: print `error: COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID must be set. Run aws_deploy/setup_cognito.py first.` and `sys.exit(1)`. Without this, every request would get a 500.

No changes to `aws_deploy/helpers/lambda_builder.py`, `aws_deploy/helpers/lambda_deployer.py`, `aws_deploy/helpers/auth.py` or `aws_deploy/setup_cognito.py`. `setup_cognito.py` already creates the `ADMIN` and `USER` groups and puts `staff1`, `admin1` and `admin2` in `ADMIN`, which is the role model 3.7 depends on.

#### One-time migration script

**File**: `scripts/migrate_assigned_to_sub.py` (`scripts/` already has `__init__.py`)

Header: a uv inline-script block like `deploy_v2.py`, with dependencies `boto3>=1.28.0`, `pymongo>=4.3.0`, `dnspython>=2.3.0`. Insert the project root into `sys.path` the same way `deploy_v2.py` does, then import `load_env_file` and `normalize_mongo_uri` from `aws_deploy.helpers.auth` and `DirectoryUser` and `resolve_recipient` from `backend_v2.shared.domain.directory_user`.

Command line (argparse):
```
uv run scripts/migrate_assigned_to_sub.py                         # dry run (default): prints the plan, writes nothing
uv run scripts/migrate_assigned_to_sub.py --apply                 # writes a backup, then updates
uv run scripts/migrate_assigned_to_sub.py --apply --backup-file PATH
uv run scripts/migrate_assigned_to_sub.py --restore PATH          # puts assigned_to back from a backup file
options: --user-pool-id ID (default env COGNITO_USER_POOL_ID)
```
`--apply` and `--restore` are mutually exclusive. The default backup path is `/tmp/assigned_to_backup_<UTC yyyymmddTHHMMSSZ>.json`, outside the repository.

Configuration: call `load_env_file()`. Read `MONGO_URI` from the environment and pass it through `normalize_mongo_uri`. If `MONGO_URI` or the pool id is missing, print an error and exit with code 2. Do not use `get_mongo_uri()`, because it prompts for input. The pool region is `pool_id.split("_", 1)[0]`. Use the `documents` collection of `MongoClient(uri).get_default_database()`.

Legacy alias table, derived from `KNOWN_USER_ALIASES` in the current handlers. Keys are compared after `.strip().casefold()`:

```python
LEGACY_ALIAS_TO_EMAIL = {
    "คุณธารินี สุขสวัสดิ์": "staff1@tu.ac.th", "staff1": "staff1@tu.ac.th", "staff-1": "staff1@tu.ac.th",
    "อ.ดร.ปกรณ์ สุขใจ": "head1@tu.ac.th", "หัวหน้าสาขาวิชาวิทยาการคอมพิวเตอร์": "head1@tu.ac.th",
    "หัวหน้าสาขา": "head1@tu.ac.th", "head1": "head1@tu.ac.th", "head-1": "head1@tu.ac.th",
    "อ.ดร.วรินทร์ พงษ์ดี": "fac_a@tu.ac.th", "fac_a": "fac_a@tu.ac.th", "fac-a": "fac_a@tu.ac.th",
    "อ.ดร.ณัฐวุฒิ ศรีวัฒน์": "fac_b@tu.ac.th", "fac_b": "fac_b@tu.ac.th", "fac-b": "fac_b@tu.ac.th",
    "kasidit": "admin1@tu.ac.th", "admin1": "admin1@tu.ac.th", "admin one": "admin1@tu.ac.th",
    "saowaluk": "admin2@tu.ac.th", "admin2": "admin2@tu.ac.th", "admin two": "admin2@tu.ac.th",
    "prapaporn": "user1@tu.ac.th", "user1": "user1@tu.ac.th", "user one": "user1@tu.ac.th",
    "songsakdi": "user2@tu.ac.th", "user2": "user2@tu.ac.th", "user two": "user2@tu.ac.th",
    "wilawan": "user3@tu.ac.th", "user3": "user3@tu.ac.th", "user three": "user3@tu.ac.th",
}
```
(`"STAFF-1"`, `"HEAD-1"`, `"FAC-A"` and `"FAC-B"` fold to the dash keys above.)

Pure functions, importable by tests without AWS or MongoDB:
```python
def load_directory_users(cognito_client, pool_id) -> list[DirectoryUser]
    # same pagination and filtering as CognitoUserDirectory.list_users

def legacy_values(record: dict) -> list[str]
    # assigned_to as a list of non-empty stripped strings (a plain string becomes a one-item list, anything else []);
    # if that list is empty and record["receiver"] is a non-empty string -> [receiver.strip()]

def resolve_legacy_identity(value: str, users: Sequence[DirectoryUser]) -> Optional[str]
    # candidate = LEGACY_ALIAS_TO_EMAIL.get(value.strip().casefold(), value)
    # try: return resolve_recipient(candidate, users).sub   (sub, then email, then unique name)
    # except ValueError: return None

@dataclass
class PlannedChange:  doc_id: str; title: str; old_assigned_to: Any; new_assigned_to: list[str]
@dataclass
class UnresolvedDoc:  doc_id: str; title: str; unresolved_values: list[str]
@dataclass
class MigrationPlan:  total: int; unchanged: int; changes: list[PlannedChange]; unresolved: list[UnresolvedDoc]

def plan_migration(records: Iterable[dict], users: Sequence[DirectoryUser]) -> MigrationPlan
    # for each record: values = legacy_values(record)
    #   no values -> unchanged
    #   resolve each; any None -> UnresolvedDoc (the document is not changed)
    #   new = resolved subs with duplicates removed, in order
    #   new == record["assigned_to"] (same list) -> unchanged; else PlannedChange(old = raw record["assigned_to"])
```

Behavior:
- **Dry run** prints one line per planned change (`<doc_id>  <title>  <old> -> <new>`), one line per unresolved document with its unresolved values, and a summary line `total=N unchanged=N to_update=N unresolved=N`. It makes no writes and creates no backup file.
- **Apply** writes the backup first, as a JSON list of `{"_id": "<hex>", "assigned_to": <old raw value>}` for every planned change, with `ensure_ascii=False`. Then, for each change, it runs `update_one({"_id": ObjectId(doc_id), "assigned_to": old_raw}, {"$set": {"assigned_to": new}})`. Matching on the old value means a concurrent edit is not overwritten. The script reports how many updates matched and prints any that did not. `receiver` and `status_history` are never modified.
- **Restore** reads the backup and runs `update_one({"_id": ObjectId(id)}, {"$set": {"assigned_to": old}})` for each entry.
- **Exit codes**: 0 when nothing is unresolved, 1 when any document is unresolved (in both dry run and apply), 2 for configuration errors.

Unresolved documents keep their legacy strings, which no `sub` matches, so non-admins lose access to them (fail closed). An admin can re-assign them through `POST /api/v1/documents/<id>/assign`. The script is idempotent: a `sub` resolves to itself, so a second run plans no changes. If anyone re-seeds MongoDB with the v1 seed scripts (`scripts/seed_mock_documents.py`), the script must be run again.

Run order on deploy day: deploy F', run the dry run, review the output, run `--apply`, keep the backup file.

#### Test scaffolding files

- `requirements-dev.txt` (new, project root): `pytest>=8.0`, `hypothesis>=6.100`, `pyjwt[crypto]>=2.10.1,<3`, `boto3>=1.28.0`, `pymongo>=4.3.0`. The root `requirements.txt` is the v1 Flask image's dependency list (used by `Dockerfile`) and must not change.
- `pytest.ini` (new, project root):
```
[pytest]
testpaths = tests
pythonpath = .
```
- Tests live in `tests/v2/` with no `__init__.py`. Do not name the folder `tests/backend_v2`, because it could collide with the `backend_v2` namespace package.
- Command, run from `CS361_G3/`: `uv run --with-requirements requirements-dev.txt pytest -q`. Equivalent without uv: `pip install -r requirements-dev.txt` then `python -m pytest -q`.

#### Documentation

Update the "Routing Matrix" in `backend_v2/README.md`: `/api/v1/auth/users` becomes "Authenticated", the `/assign` row becomes "ADMIN or assigned user", and add one sentence under "Security & Compliance Highlights" that tokens are verified in each Lambda against the Cognito JWKS. Change nothing else in the README.

## Testing Strategy

### Validation Approach

The testing strategy follows a two-phase approach: first, surface counterexamples that demonstrate the bug on unfixed code, then verify the fix works correctly and preserves existing behavior.

All tests are offline. No Cognito, MongoDB, S3 or network access.

**Shared fixtures**, in `tests/v2/conftest.py`:
- `rsa_private_key` (session scope): `cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key(public_exponent=65537, key_size=2048)`. A second key, `other_private_key`, signs "foreign" tokens.
- Constants: `TEST_POOL_ID = "us-east-1_TestPool1"`, `TEST_CLIENT_ID = "test-client-id"`, `TEST_KID = "test-kid-1"`, `TEST_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool1"`.
- `make_token(claims_overrides=None, key=None, kid=TEST_KID, alg="RS256") -> str`: builds default claims `{"sub", "email", "name", "cognito:groups", "iss": TEST_ISSUER, "aud": TEST_CLIENT_ID, "token_use": "id", "iat": now, "exp": now + 3600}`, applies the overrides (a value of `None` deletes the claim), and signs with `jwt.encode(claims, key or rsa_private_key, algorithm=alg, headers={"kid": kid})`.
- `make_none_token(claims) -> str`: base64url header `{"alg":"none","typ":"JWT"}` + base64url payload + `.mock_signature`, the same format `_synthesize_test_token` produces.
- `StaticSigningKeyProvider(keys: dict[str, public_key])` with `get_signing_key_from_jwt(token)`: reads `kid` from `jwt.get_unverified_header`, returns `types.SimpleNamespace(key=keys[kid])`, and raises `jwt.PyJWKClientError("Unable to find a signing key")` for an unknown `kid`. A variant raises `jwt.PyJWKClientConnectionError` to test 503.
- `verifier` fixture: `CognitoTokenVerifier(TEST_POOL_ID, TEST_CLIENT_ID, signing_key_provider=StaticSigningKeyProvider({TEST_KID: rsa_private_key.public_key()}))`.
- Autouse fixture `install_test_verifier`: sets env `COGNITO_USER_POOL_ID` and `COGNITO_CLIENT_ID` with `monkeypatch`. It then tries `from backend_v2.shared.auth import set_token_verifier` and, when the import succeeds, calls `set_token_verifier(verifier)` and resets it to `None` after the test. On unfixed code the import fails and the fixture does nothing, so the same test files run against F and F'.
- In-memory adapters:
  - `InMemoryDocumentRepo(DocumentRepositoryPort)`: a dict store with ids `"doc-1"`, `"doc-2"` and so on. `find_by_assigned_user(value)` returns documents whose `assigned_to` contains `value` exactly. When `value` is a list (F passes its identity list), it returns documents whose `assigned_to` contains any element exactly. This lets the same repo serve F and F'.
  - `InMemoryFileStorage(FileStoragePort)`: writes files under `tmp_path`.
  - `FakeUserDirectory`: returns a fixed list built from the nine `SEED_USERS` in `aws_deploy/setup_cognito.py`, each with a fixed UUID `sub`. It subclasses `UserDirectoryPort` when that import succeeds and is a plain class otherwise.
- `build_document_use_cases(repo, storage, directory)`: calls `DocumentUseCases(repository=repo, storage=storage, user_directory=directory)` and, if that raises `TypeError` (F has no `user_directory` parameter), calls it without `user_directory`.
- `make_event(method, path, token=None, via="bearer"|"cookies"|"cookie_header", body=None, headers=None, query=None) -> dict`: builds an HTTP API v2 event with `requestContext.http.method`, `rawPath`, and the token in the chosen place.
- `FakeCognitoClient`: `initiate_auth` returns `{"AuthenticationResult": {"IdToken": <token>}}`, or raises `botocore.exceptions.ClientError({"Error": {"Code": code, "Message": "x"}}, "InitiateAuth")`, or raises `botocore.exceptions.EndpointConnectionError(endpoint_url="https://cognito-idp")`.

Handlers are wired through their existing `set_use_cases()` hooks with use cases built on these fakes.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix. Confirm or refute the root cause analysis. If we refute, we will need to re-hypothesize.

**Test Plan**: Write `tests/v2/test_bug_condition_exploration.py`. It calls only `lambda_handler` and `set_use_cases()`, which exist in F and F', so it runs unchanged on both. Run it on the UNFIXED code and record the failures. Every test asserts the F' behavior, so each should fail on F.

**Test Cases**:
1. **Unsigned admin token**: `make_none_token({"sub": "attacker", "cognito:groups": ["ADMIN"]})` to `GET /api/v1/documents` must return 401 (will fail on unfixed code: returns 200 with all documents).
2. **Foreign-key, expired, wrong-audience, wrong-issuer and access tokens**: each to `GET /api/v1/search/trie` must return 401 (will fail on unfixed code).
3. **Display-name escalation**: a validly signed `USER` token with `name="Staff Helper"` posting a multipart document must return 403 (will fail on unfixed code: 201).
4. **`custom:role` escalation**: a `USER` token with `"custom:role": "admin"` to `GET /api/v1/documents` must not see documents it is not assigned to (will fail on unfixed code).
5. **Name collision**: a `USER` token for `fac_a` with `name` set to FAC-B's name, requesting FAC-B's document file, must return 403 (will fail on unfixed code).
6. **Login fallbacks**: `AuthUseCases(cognito_client=FakeCognitoClient(raises EndpointConnectionError), cognito_client_id="x")` with `admin@kanna.ac.th` / `admin1234` must return 503 and no token (will fail on unfixed code: 200 and an `alg: none` token). Construct `AuthUseCases` with only the keyword arguments both versions accept (`cognito_client`, `cognito_client_id`). If `AuthUseCases` has a `_get_user_collection` attribute (F only), monkeypatch it to return `None`, so F does not wait 30 seconds for a local MongoDB and reaches the dev admin branch.
7. **Login role heuristic**: Cognito returns a signed token for `admin9@tu.ac.th` in group `USER`; the login response role must be `"user"` (will fail on unfixed code).
8. **Unauthenticated users list**: `GET /api/v1/auth/users` with no token must return 401 (will fail on unfixed code).
9. **Unverified `/auth/me`**: an `alg: none` token must return 401 (will fail on unfixed code).
10. **Error leak**: a repo whose `find_all` raises `RuntimeError("secret-host:27017")`, called by an admin, must return 500 with body exactly `{"error":{"message":"Internal server error"}}` (will fail on unfixed code).
11. **Identity logging**: with `capsys`, any authenticated documents request must print nothing that contains the caller's email (will fail on unfixed code: `[AUTH_DEBUG]` line).

**Expected Counterexamples**:
- Forged and foreign tokens return 200 with data.
- Non-admin users become admin through `name`, `custom:role` or email text.
- Login succeeds with Cognito down and returns a token ending in `.mock_signature`.
- 500 bodies contain the exception text; stdout contains `[AUTH_DEBUG]`.
- Possible causes: no signature check in `_decode_jwt_payload`, heuristics in `_parse_auth` and `login`, fail-open branches in `login`, name matching in `is_accessible_by`.

If a test passes on F, the root cause analysis for that path is wrong. Stop and re-check before writing the fix.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed function produces the expected behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := lambda_handler_fixed(input.event)
  ASSERT expectedBehavior(input, result)
END FOR

FUNCTION expectedBehavior(input, result)
  IF C1 OR C4 (bad or missing token on an authenticated route):
      RETURN result.statusCode == 401
             AND result.body.error.message IN {"Authentication is required", "Invalid authentication token"}
             AND no use-case method was called
  IF C2 (heuristic admin):
      RETURN result equals the response for the same request with is_admin = ("ADMIN" IN groups)
  IF C3 (login without Cognito authentication):
      RETURN result.statusCode IN {401, 503} AND "token" NOT IN result.body AND no Set-Cookie
  IF C5 (non-sub identity match):
      RETURN (route IN {details, file, status, assign} AND result.statusCode == 403)
          OR (route IN {list, trie} AND doc.id NOT IN ids(result.body))
  IF C6: RETURN result.statusCode == 500 AND result.body == {"error": {"message": "Internal server error"}}
  IF C7: RETURN captured stdout and log records contain none of claims.email, claims.name, claims.sub, groups
END FUNCTION
```

The exploration tests from the previous section become the fix-checking unit tests. After the fix they must pass with no edits.

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed function produces the same result as the original function.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT lambda_handler_original(input.event) = lambda_handler_fixed(input.event)
END FOR
```

**Testing Approach**: Property-based testing is recommended for preservation checking because:
- It generates many test cases automatically across the input domain
- It catches edge cases that manual unit tests might miss
- It provides strong guarantees that behavior is unchanged for all non-buggy inputs

**Test Plan**: Observe behavior on UNFIXED code first for non-bug inputs, then write property-based tests capturing that behavior. F and F' cannot be imported side by side, so "same result" is checked by asserting the observed F behavior as fixed expectations: write `tests/v2/test_preservation.py`, run it on F and confirm it passes, then keep it passing on F'.

Hypothesis generators keep inputs outside C:
- Users are drawn from the nine seed users. For non-admin users, generated `email` and `name` values never contain "admin" or "staff" (use `st.text(alphabet=string.ascii_lowercase + "ก-ฮ")` filtered on the casefolded value) and never include `custom:role` or `role`.
- Every token is signed with `rsa_private_key`, carries correct `iss`, `aud` and `token_use`, and expires in the future.
- Documents have `assigned_to` containing only `sub` values, and `receiver` set to the assigned user's display name (so F's `receiver` match gives the same answer as F's `sub` match).
- The token location is drawn from `bearer`, `cookies` and `cookie_header`.

**Test Cases**:
1. **Admin access preservation**: for a random `ADMIN` user and random document set, list returns every document; details, file (with and without `download=true`), status changes and assign succeed with the same status codes and bodies as on F.
2. **Assigned recipient preservation**: for a random `USER` user assigned to a random subset of documents, list and trie contain exactly that subset; details and file return 200; status follows `ALLOWED_STATUS_TRANSITIONS` (valid gives 200, invalid gives 400 with "Invalid workflow transition: ...").
3. **Non-assigned preservation**: for documents not containing the caller's `sub`, details, file, status and assign return 403 with the existing messages, and the documents are absent from list and trie.
4. **Assign semantics preservation**: assigning to a recipient given as a `sub` replaces `assigned_to`, moves RECEIVED to ASSIGNED or ASSIGNED to FORWARDED, and appends exactly one history entry whose `changed_by` is the caller's email.
5. **Login and logout preservation**: a `FakeCognitoClient` returning a signed token gives 200, the same user object keys, `role` from groups, and the exact cookie string; `NotAuthorizedException` gives 401 "Invalid email or password"; empty fields give 400; logout gives 200 and the clearing cookie.
6. **Registration preservation**: an admin registering a document addressed to a seed user's exact display name gets 201 with the same `message`, `title`, `sender`, `receiver`, `status` (ASSIGNED) and history length on F and F'. `assigned_to` is excluded from this comparison, because F stores the name and F' stores the `sub` (an intentional difference).
7. **Request validation preservation**: unknown ids give 404, bad JSON gives 400, non-multipart registration gives 400 "Content-Type must be multipart/form-data", OPTIONS gives 204 with the same headers, and a missing token gives 401 "Authentication is required".

### Unit Tests

- `tests/v2/test_token_verifier.py`: valid token returns claims; each failure rejects with `INVALID_TOKEN_MESSAGE` (wrong key, `alg: none`, `HS256` signed with the public key bytes, missing `kid`, unknown `kid`, expired, `iat` in the future beyond leeway, wrong `aud`, wrong `iss`, `token_use="access"`, missing `sub`, malformed string); empty token gives `AUTH_REQUIRED_MESSAGE`; connection error gives `AuthServiceUnavailableError`; malformed pool id or empty client id raises `RuntimeError`; the exception message never contains library text.
- `tests/v2/test_caller.py`: `caller_from_claims` field mapping, `is_admin` only for exact `"ADMIN"`, `role` values, `actor_label` fallback to `sub`, missing `sub` raises `AuthenticationError`, `repr(caller)` does not contain claim values.
- `tests/v2/test_request_auth.py`: `extract_token` order and parsing for the three locations, including an empty `Bearer ` header; `authenticate` with no token raises before the verifier is built (no env set).
- `tests/v2/test_directory_user.py`: `resolve_recipient` by `sub`, by email in any case, by unique name, ambiguous name, unknown value, empty and non-string input; `resolve_recipients` order and de-duplication and the empty-list message.
- `tests/v2/test_cognito_user_directory.py`: a fake client with `get_paginator("list_users")` over two pages; disabled users and users without `sub` are skipped; sorting; the cache is reused within the TTL and refreshed after it (inject `clock`); missing pool id raises `RuntimeError`.
- `tests/v2/test_document_access.py`: `Document.is_accessible_by` exact and case-sensitive, no `receiver` match, list input returns `False`.
- `tests/v2/test_mongo_repository_query.py`: replace `repo._collection` with a fake that records the `find` filter; `find_by_assigned_user("abc")` issues exactly `{"assigned_to": "abc"}`; empty or non-string input returns `[]` without querying.
- `tests/v2/test_documents_handler.py`, `tests/v2/test_search_handler.py`, `tests/v2/test_auth_handler.py`: every row of the HTTP status and message table; register with an unknown recipient returns 400 and leaves no file in `InMemoryFileStorage`; `/auth/users` returns `[{"id", "name"}]` for a `USER` caller; `/auth/me` returns the verified claims; a `PermissionError` from a use case in the auth handler stays 401 while `AuthenticationError` is 401 and `AuthServiceUnavailableError` is 503.
- `tests/v2/test_migration_plan.py`: `legacy_values`, `resolve_legacy_identity` for each alias group in `LEGACY_ALIAS_TO_EMAIL`, unresolved documents are reported and unchanged, a dry run issues no writes (fake collection records calls), apply writes the backup before the first `update_one`, and restore puts values back.

### Property-Based Tests

All use Hypothesis with `@settings(max_examples=100, deadline=None)` (RSA signing makes per-example time uneven).

- **Property 1 (`tests/v2/test_bug_condition_exploration.py`, tagged `# Feature: backend-v2-auth-bypass-fix, Property 1`)**: generate random claim sets (random `sub`, `email`, `name`, `cognito:groups` subsets of `["ADMIN","USER","STAFF","admin"]`, optional `custom:role`) combined with a random defect: unsigned, foreign key, expired, wrong `aud`, wrong `iss`, wrong `token_use`. Every documents and search route must return 401 and call no use case. Separately, for validly signed tokens, `is_admin` must equal `"ADMIN" in groups`, whatever the other claims say.
- **Property 2 (`tests/v2/test_preservation.py`, Property 2)**: the generators described under Preservation Checking, asserting the observed F responses.
- **Property 3 (`tests/v2/test_role_consistency.py`, Property 3)**: for random verified claims, the role from `AuthUseCases.login` (with a `FakeCognitoClient` returning that token), the `is_admin` behavior of the documents handler (admin sees an unassigned document or not), and `caller_from_claims(claims).role` agree, and all equal `"admin"` exactly when `"ADMIN" in cognito:groups`.
- **Property 4 (`tests/v2/test_migration_plan.py`, Property 4)**: generate a random directory (unique `sub` values, random names with deliberate duplicates) and random legacy records whose values are drawn from subs, emails in random case, alias keys and names. Assert: each planned value equals the `sub` the value identifies; records with an unknown or ambiguous name are in `unresolved` and not in `changes`; `plan_migration` applied to the planned output yields no changes (idempotence).

### Integration Tests

Offline, in `tests/v2/test_integration_flows.py`, with the real `CognitoTokenVerifier` (static key provider), real use cases and the in-memory adapters:
- **Full routing flow**: an admin logs in through the auth handler (fake Cognito returns a signed token), registers a document to "อ.ดร.ปกรณ์ สุขใจ" by name (status ASSIGNED), HEAD-1 logs in, lists it, and forwards it to FAC-B by name (ASSIGNED to FORWARDED). FAC-B logs in, lists it, acknowledges it (FORWARDED to ACKNOWLEDGED, taken from `ALLOWED_STATUS_TRANSITIONS`) and downloads it. HEAD-1 now gets 403 on details, file and status change, and sees nothing in the list and the trie. The order is forward first, then acknowledge, because the state machine does not allow ACKNOWLEDGED to FORWARDED.
- **Token transport**: the same token works through the Bearer header, `event["cookies"]` and the `Cookie` header, and the `auth_token` cookie value from the login response authenticates the next request.
- **Cross-Lambda consistency**: one token gives the same admin decision in the documents, search and auth (`/auth/me`) handlers.

Post-deployment smoke check (manual, after `uv run aws_deploy/deploy_v2.py` and the migration dry run). The executor runs these with `curl` against the API endpoint printed by the deployment and records the results:
1. `GET /api/v1/documents` with no token returns 401 "Authentication is required".
2. The same request with an `alg: none` token returns 401 "Invalid authentication token".
3. Login as `staff1@tu.ac.th` returns 200, role `admin`, and a three-part RS256 token.
4. `GET /api/v1/auth/users` with that token returns 200 and a list of `{id, name}`. A 500 here means `LabRole` cannot call `cognito-idp:ListUsers` (check CloudWatch for `AccessDeniedException`). Stop and report to the user (decision D4).
5. Login as `head1@tu.ac.th` returns role `user`. After the migration is applied, HEAD-1's existing documents are still listed.
6. CloudWatch logs for one documents request contain no `[AUTH_DEBUG]` line and no email address.
