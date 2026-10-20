# Auth Micro-Lambda Function

<p align="center">
  <img src="https://img.shields.io/badge/Function-Auth%20Micro--Lambda-purple?logo=amazon-aws&logoColor=white" alt="AWS Lambda" />
  <img src="https://img.shields.io/badge/Auth-AWS%20Cognito%20User%20Pool-orange?logo=json-web-tokens&logoColor=white" alt="Cognito" />
  <img src="https://img.shields.io/badge/Session-HttpOnly%20Cookie-brightgreen" alt="Session" />
</p>

---

## 📖 Overview

The **Auth Micro-Lambda** (`functions/auth/`) manages user authentication, Cognito token exchange, and session lifecycles for e-Mailbox.

In accordance with modern security standards, JWT tokens are never stored in client-side storage (`localStorage`/`sessionStorage`). Instead, authentication state is communicated via **`HttpOnly; Secure; SameSite=None` Cookies**.

---

## 🗺️ Route Specification

| Method | Route Path | Access Control | Description |
| :---: | :--- | :---: | :--- |
| `POST` | `/api/v1/auth/login` | Public | Authenticates via Cognito User Pool, returns user profile, issues `HttpOnly` cookie |
| `POST` | `/api/v1/auth/logout` | Authenticated | Clears `auth_token` session cookie (`Max-Age=0`) |
| `GET` | `/api/v1/auth/users` | Authenticated | Returns distinct recipient user names for Admin document routing |
| `GET` | `/api/v1/auth/me` | Authenticated | Returns currently authenticated user identity and claims |

---

## ⚙️ Environment Variables

| Variable Name | Required | Description |
| :--- | :---: | :--- |
| `COGNITO_USER_POOL_ID` | **Yes** | AWS Cognito User Pool ID |
| `COGNITO_CLIENT_ID` | **Yes** | App Client ID (`ALLOW_USER_PASSWORD_AUTH`) |
| `FRONTEND_ORIGIN` | **Yes** | Exact frontend domain for strict CORS cookie exchange |
| `MONGO_URI` | Optional | Connection string for reading user recipient directory |
