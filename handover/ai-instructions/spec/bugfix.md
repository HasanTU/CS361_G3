# Bugfix Requirements Document

## Introduction

Access control in backend_v2 can be bypassed. The auth, documents and search Lambdas decode the login token's payload without checking its signature, issuer, audience or expiry, and API Gateway has no authorizer in front of them. Anyone who can build a token can claim to be an admin or any other user. The per-document access check runs, but it trusts that forged identity, so it does not protect documents or files.

Several defects make this worse:

- Admin status is guessed from text, including the user-editable display name.
- The server mints `alg: none` tokens and accepts them.
- A hard-coded dev admin login and a plaintext password compare act as fallbacks when Cognito errors.
- `GET /api/v1/auth/users` needs no login.
- Documents are matched to users by display name, email and alias instead of the Cognito `sub`.
- Unhandled errors return exception text to the client, and identities are written to logs.
- `/api/v1/auth/me` echoes unverified claims.

This spec covers findings 1 to 7 and 15 of `project-requirements/security-review-backend-v2.md`. The following are out of scope and will be handled in separate specs:

- A system-generated document number.
- Multi-recipient delivery and forwarding that does not overwrite `assigned_to`.
- CORS origin reflection, cookie and token storage, upload content-type checks, rate limiting, access logging, deployment secrets and VPC.

Until the separate forwarding spec lands, a document keeps its current single-recipient behavior. Only the way recipients are identified changes, from display names to Cognito `sub` values.

The role model stays the one the Cognito user pool defines today: users in the `ADMIN` group are admins, and every other authenticated user is a regular user.

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN a request to the documents or search Lambda carries a token whose signature is missing, invalid, or not issued by the project's Cognito user pool THEN the system accepts the token and uses its claims as the caller's identity and role

1.2 WHEN a request carries a token that is expired, or whose issuer or audience does not match the project's Cognito user pool and app client THEN the system accepts the token as valid

1.3 WHEN a login falls through to the MongoDB fallback or the dev admin account THEN the system mints a token with header `alg: none` and signature `mock_signature`, and the documents and search Lambdas accept it

1.4 WHEN any identity value in the token (email, `name`, `cognito:username`, `username` or `sub`) contains "admin" or "staff", or matches a hard-coded email or name list THEN the system grants admin privileges regardless of Cognito group membership

1.5 WHEN a token carries a `custom:role` or `role` claim equal to "admin" THEN the system grants admin privileges regardless of Cognito group membership

1.6 WHEN a user logs in with an email that starts with "admin" or appears in a hard-coded admin email list THEN the system returns role "admin" in the login response regardless of Cognito group membership

1.7 WHEN the same caller's role is determined by the login use case and by the documents and search handlers THEN the system applies different rules in each place, so the role shown at login can differ from the role enforced by the API

1.8 WHEN Cognito is unreachable or the boto3 call fails with an error other than NotAuthorizedException or UserNotFoundException THEN the system falls back to a MongoDB lookup that accepts a stored `password_hash` equal to the plaintext password

1.9 WHEN Cognito is unreachable or not configured and the credentials are `admin@kanna.ac.th` / `admin1234` THEN the system logs the caller in as an admin

1.10 WHEN an unauthenticated client calls `GET /api/v1/auth/users` THEN the system returns the list of user names from MongoDB plus a hard-coded list of names

1.11 WHEN a non-admin caller requests a document list, document details, a document file, a status change, a forward, or the search trie THEN the system grants access if any of the caller's email, display name, username, `sub` or `KNOWN_USER_ALIASES` expansions matches an `assigned_to` entry or the `receiver` field (case-insensitive)

1.12 WHEN a caller sets their display name, or another identity value, to match another user's display name, email or alias THEN the system gives the caller that user's documents and files

1.13 WHEN an unhandled exception occurs in the auth, documents or search Lambda THEN the system returns `Internal server error: <exception text>` to the client

1.14 WHEN the documents or search Lambda authenticates a request THEN the system prints the caller's identities, admin flag, groups and role to the logs (`[AUTH_DEBUG]`)

1.15 WHEN a client calls `GET /api/v1/auth/me` with any token THEN the system returns the raw, unverified token claims

### Expected Behavior (Correct)

2.1 WHEN a request to the documents or search Lambda carries a token whose signature is missing, invalid, or not issued by the project's Cognito user pool THEN the system SHALL reject the request with HTTP 401 and SHALL NOT run any document or search operation

2.2 WHEN a request carries a token that is expired, or whose issuer or audience does not match the project's Cognito user pool and app client THEN the system SHALL reject the request with HTTP 401

2.3 WHEN any code path issues or accepts a token THEN the system SHALL only issue tokens returned by Cognito and SHALL reject tokens with `alg: none` or any algorithm other than the one Cognito signs with

2.4 WHEN the system determines whether a caller is an admin THEN the system SHALL grant admin privileges only if the verified `cognito:groups` claim contains `ADMIN`, and SHALL ignore email, display name, username and `sub` text

2.5 WHEN a token carries a `custom:role` or `role` claim THEN the system SHALL ignore that claim for authorization

2.6 WHEN a user logs in THEN the system SHALL derive the role in the login response only from the verified `cognito:groups` claim

2.7 WHEN the caller's role is determined anywhere in the auth, documents or search Lambdas THEN the system SHALL use one shared role check, so the login response, `/api/v1/auth/me` and every API authorization decision agree

2.8 WHEN Cognito is unreachable or the boto3 call fails for any reason other than invalid credentials THEN the system SHALL refuse the login with an error response, SHALL NOT fall back to MongoDB or any local credential check, and SHALL NOT compare passwords in plaintext

2.9 WHEN any client submits the credentials `admin@kanna.ac.th` / `admin1234` THEN the system SHALL reject the login unless that account exists in Cognito and Cognito authenticates it

2.10 WHEN an unauthenticated client calls `GET /api/v1/auth/users` THEN the system SHALL reject the request with HTTP 401, and WHEN an authenticated client calls it THEN the system SHALL return only the user id (Cognito `sub`) and display name of registered users, with no hard-coded names

2.11 WHEN a non-admin caller requests a document list, document details, a document file, a status change, a forward, or the search trie THEN the system SHALL grant access only if the caller's verified Cognito `sub` is in the document's `assigned_to` list

2.12 WHEN a caller's display name, email or username matches another user's display name, email or alias THEN the system SHALL NOT give the caller access to that user's documents or files

2.13 WHEN an unhandled exception occurs in the auth, documents or search Lambda THEN the system SHALL return HTTP 500 with a generic message that contains no exception text, and SHALL write the details to the logs only

2.14 WHEN the documents or search Lambda authenticates a request THEN the system SHALL NOT write the caller's identities, groups or token claims to the logs

2.15 WHEN a client calls `GET /api/v1/auth/me` THEN the system SHALL return the caller's claims only after verifying the token, and SHALL return HTTP 401 for a missing or invalid token

### Unchanged Behavior (Regression Prevention)

3.1 WHEN a registered Cognito user logs in with correct credentials THEN the system SHALL CONTINUE TO return the user object (id, email, name, role, faculty) and the Cognito token, and to set the `auth_token` HttpOnly cookie

3.2 WHEN a user logs in with a wrong password or an unknown email THEN the system SHALL CONTINUE TO return HTTP 401 with "Invalid email or password"

3.3 WHEN a login request has an empty email or password THEN the system SHALL CONTINUE TO return HTTP 400

3.4 WHEN a client calls `POST /api/v1/auth/logout` THEN the system SHALL CONTINUE TO return success and clear the `auth_token` cookie

3.5 WHEN a request carries a valid Cognito token in the `Authorization: Bearer` header or in the `auth_token` cookie THEN the system SHALL CONTINUE TO authenticate the caller

3.6 WHEN a request has no token THEN the system SHALL CONTINUE TO return HTTP 401 "Authentication is required"

3.7 WHEN a verified user in the Cognito `ADMIN` group (for example `staff1@tu.ac.th`, `admin1@tu.ac.th`, `admin2@tu.ac.th`) calls the documents or search API THEN the system SHALL CONTINUE TO let them list all documents, register documents, assign and forward any document, change any document's status, view and download any file, and get the search trie over all documents

3.8 WHEN a verified non-admin user calls `POST /api/v1/documents` THEN the system SHALL CONTINUE TO return HTTP 403

3.9 WHEN a verified non-admin user is the assigned recipient of a document THEN the system SHALL CONTINUE TO let them list it, view its details, view and download its file, update its status along the allowed workflow transitions, forward it, and see it in the search trie

3.10 WHEN a verified non-admin user is not assigned to a document THEN the system SHALL CONTINUE TO return HTTP 403 for its details, file, status change and forward, and SHALL CONTINUE TO leave it out of their document list and search trie

3.11 WHEN a document is assigned or forwarded THEN the system SHALL CONTINUE TO replace `assigned_to` with the new recipients, move the status to ASSIGNED or FORWARDED, and append an entry to the status history

3.12 WHEN a document id does not exist THEN the system SHALL CONTINUE TO return HTTP 404

3.13 WHEN a status change is not an allowed workflow transition, or a request body or multipart form is invalid THEN the system SHALL CONTINUE TO return HTTP 400 with a descriptive message

3.14 WHEN a file is requested with or without `download=true` THEN the system SHALL CONTINUE TO serve it as an attachment or inline respectively, with the stored file name

3.15 WHEN a client sends an OPTIONS preflight request THEN the system SHALL CONTINUE TO return HTTP 204 with the CORS headers

3.16 WHEN documents stored before the fix were assigned to a legitimate recipient by name, email or alias THEN the system SHALL CONTINUE TO give that same recipient access after the one-time migration to Cognito `sub` values

3.17 WHEN unit tests replace the use cases through `set_use_cases()` THEN the system SHALL CONTINUE TO allow the handlers to run offline without Cognito or MongoDB
