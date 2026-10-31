# Backlog after the auth fix

Possible work, not started. Each item should get its own spec and the owner's go-ahead. Priorities are suggestions. Items marked "not tested" come from reading the code.

Finish the current spec first (deploy, smoke check, migration). Until then a deployed API still has the original bypass.

## Security

### S1. CSRF through the `SameSite=None` cookie (High, not tested)
- **Problem:** login sets `auth_token=<token>; HttpOnly; Secure; SameSite=None`, and the Lambdas accept the cookie when no Bearer header is sent. A page on another site can submit a plain HTML form to `POST /api/v1/documents/{id}/assign`; with `enctype="text/plain"` the body can be valid JSON. The browser attaches the cookie, so the request runs as the logged-in user. Forwarding a document to the attacker's own account would give them access. CORS doesn't stop this, because simple form posts have no preflight. `POST /api/v1/documents` (multipart) is exposed the same way for admins.
- **Where:** `backend_v2/functions/auth/handler.py` (cookie), `backend_v2/shared/auth/request_auth.py` (`extract_token`), `backend_v2/functions/documents/handler.py` (assign parses any body with `json.loads`).
- **Options:** (a) accept only the Bearer header on state-changing routes; the frontend already prefers it (commit `97bf997`), but this changes preserved requirement 3.5. (b) Require `Content-Type: application/json` on JSON routes plus a custom header such as `X-Requested-With` on every state-changing route; a cross-site form can set neither without a preflight. (c) `SameSite=Strict` or `Lax`: the frontend (S3 website) and the API (execute-api) are different sites, so this works out the same as (a).
- **Check:** a cookie-only `text/plain` POST to `/assign` gets 4xx and changes nothing.

### S2. Send recipients as `sub`, and lock the `name` attribute (High)
- **Problem:** the frontend sends display names (`frontend/html/staff_upload.html` posts `receiver`; `frontend/html/lecturer_view.html` posts `recipients` from a hard-coded list). Users can change their own Cognito `name`; copying someone else's makes that name ambiguous, so sends to it fail with 400 (design D5).
- **Fix:** load `GET /api/v1/auth/users` (`{id, name}`) in the frontend and send `id`. Remove `name` from the Cognito app client's writable attributes.

### S3. Files above about 4.5 MB fail (High for users)
- **Problem:** a synchronous Lambda call accepts and returns at most 6 MB. Files pass through the Lambda base64-encoded (about 33% larger), so uploads and downloads above roughly 4.5 MB fail. Every byte also goes through Lambda memory and `/tmp`.
- **Fix:** after the authorization check, return a short-lived S3 presigned GET URL (for example 60 seconds) instead of the body. For uploads, use a presigned POST with size and content-type conditions, then a confirm call that creates the record.
- **Where:** `backend_v2/shared/adapters/s3_file_storage.py`, documents file and register routes.

### S4. S3 bucket hardening (Medium, small)
- **Today:** `setup_document_bucket` in `aws_deploy/helpers/s3_storage.py` only creates the bucket and uploads seed PDFs. No public access block, bucket policy, encryption setting or versioning is configured; protection relies on AWS defaults for new buckets (not verified on the lab account). The Lambdas reach S3 through `LabRole` credentials, and files are only served after the token and `sub` checks; the browser never gets an S3 URL.
- **Fix:** `put_public_access_block` with all four settings true; a bucket policy that denies requests when `aws:SecureTransport` is false; `put_bucket_encryption` (SSE-S3); `put_bucket_versioning` plus a lifecycle rule that expires noncurrent versions.
- **Check:** `get-public-access-block`, `get-bucket-policy`, `get-bucket-encryption` after deploy.

### S5. Upload checks and safe file serving (Medium, not tested)
- **Problem:** the stored content type comes from the client, and files are served `inline` with it. An HTML or SVG upload would run in the API's origin. Only admins can upload now, which limits this.
- **Fix:** allow-list types (PDF), check magic bytes, cap the size; on file responses add `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`, and force `attachment` for anything that isn't a PDF.

### S6. CORS origin reflection (Medium)
- **Problem:** the Lambdas set `Access-Control-Allow-Origin` to the request's `Origin` with credentials allowed. `configure_apigw_v2` also sets an allow-list on API Gateway, which should take over in the deployed API, but the real headers haven't been checked.
- **Fix:** echo only allow-listed origins in the Lambdas (the same list as `configure_apigw_v2`). Check with `curl -i -H "Origin: https://evil.example" <endpoint>/api/v1/documents`.

### S7. Session lifetime and logout (Medium)
- **Today:** cookie 24 hours, ID token 1 hour by default, no refresh flow; logout only clears the cookie, so a copied token works until it expires.
- **Fix:** match the cookie `Max-Age` to the token lifetime; add a refresh-token flow with the refresh token in an HttpOnly cookie scoped to `/api/v1/auth`; on logout call Cognito `RevokeToken`.

### S8. Secrets out of Lambda environment variables (Medium, lab-dependent)
- `MONGO_URI`, including credentials, is a plain Lambda environment variable, readable by anyone with `lambda:GetFunctionConfiguration`. Move it to SSM Parameter Store (SecureString) or Secrets Manager and read it once per cold start. Use a MongoDB user limited to this database. Check that `LabRole` can read it.

### S9. Throttling and audit logs (Medium)
- Add API Gateway stage throttling, with a lower limit on `POST /api/v1/auth/login`. Cognito has its own lockout; throttling also caps cost.
- Enable API Gateway access logs. Log document access as structured events with `sub`, document id and action, without names or emails.

### S10. Least-privilege IAM (Low in the lab)
- All three Lambdas run as the broad `LabRole`. Ideal: one role per function with only `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `arn:aws:s3:::cs361-g3-documents-<account>/uploads/*`, `cognito-idp:ListUsers` on the pool, and CloudWatch Logs. AWS Academy usually doesn't allow creating roles.

### S11. VPC (Low, not recommended in the lab)
- A Lambda in a VPC has no internet access but still needs Cognito (keys and `ListUsers`) and MongoDB Atlas. That means a NAT gateway (hourly cost) or private endpoints; only S3 has a free gateway endpoint. Cold starts get slower.
- The real gain: a NAT gateway with an Elastic IP lets the Atlas IP access list allow only that IP. Atlas likely accepts any IP today because Lambda IPs change (not verified).

### S12. API Gateway JWT authorizer as a second layer (Low)
- D1 rejected it as the only check. Once the frontend uses the Bearer header only (S1 option a), an authorizer on the documents and search routes adds defense in depth.

## Features

### F1. System-generated document number (from the owner's original request)
- Today only the MongoDB ObjectId identifies a document.
- Suggestion: a per-year running number such as `2025/0001`, issued atomically with `find_one_and_update({"_id": "doc-2025"}, {"$inc": {"seq": 1}}, upsert=True, return_document=AFTER)` on a `counters` collection, plus a unique index on the number. Show it in the UI and index it in the search trie.

### F2. Multi-recipient delivery and non-destructive forwarding (from the owner's original request)
- Today a forward replaces `assigned_to`, so a document sits with one person and earlier recipients lose access.
- Suggestion: per-recipient delivery records (`deliveries: [{sub, from_sub, at, status}]`), authorize when the caller has a delivery, keep `status_history` for audit. Decide with the owner whether earlier recipients keep read access.

### F3. Forward after acknowledge
- Found in task 3.14: `ALLOWED_STATUS_TRANSITIONS` has no ACKNOWLEDGED to FORWARDED, so a recipient who acknowledged can't forward. Ask the owner if that's intended. A change is one line in `backend_v2/shared/domain/document_status.py` plus tests.

### F4. Recipient picker from the directory
- Replace the hard-coded list in `frontend/html/lecturer_view.html` with `GET /api/v1/auth/users`. Same work as S2.

### F5. Thai file names (not tested)
- `S3FileStorageAdapter._sanitize_filename` keeps only `[A-Za-z0-9_.-]` and then strips leading and trailing `.` and `_`. A Thai name such as `หนังสือเชิญ.pdf` therefore becomes `pdf`, with no extension, and that is the name users download.
- Fix: keep the original name in MongoDB for display, sanitize only the S3 key, and send `Content-Disposition` with `filename*=UTF-8''<percent-encoded name>` plus an ASCII fallback.

### F6. Pagination
- `GET /api/v1/documents` returns every document to admins. Add `limit` and cursor parameters before the data grows.

### F7. CI
- Add a GitHub Actions workflow that runs `uv run --with-requirements requirements-dev.txt pytest -q` on every push and pull request.
