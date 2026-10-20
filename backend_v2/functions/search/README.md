# Search Micro-Lambda Function

<p align="center">
  <img src="https://img.shields.io/badge/Function-Search%20Micro--Lambda-purple?logo=amazon-aws&logoColor=white" alt="AWS Lambda" />
  <img src="https://img.shields.io/badge/Trigger-API%20Gateway%20HTTP%20API%20v2-blue?logo=amazon-apigateway&logoColor=white" alt="API Gateway" />
  <img src="https://img.shields.io/badge/Latency-%3C15ms-brightgreen" alt="Latency" />
</p>

---

## 📖 Overview

The **Search Micro-Lambda** (`functions/search/`) serves the in-memory prefix tree (Trie) index used by the frontend navigation search bar. 

It compiles document metadata (`title`, `sender`, `receiver`) into a serialized prefix tree with all suffix variations, enabling instantaneous sub-millisecond client-side prefix matching across compound phrases and words.

---

## 🗺️ Route Specification

| Method | Route Path | Access Control | Description |
| :---: | :--- | :---: | :--- |
| `GET` | `/api/v1/search/trie` | Role-Filtered | Returns serialized JSON Trie (`ADMIN` indexes all docs; `USER` indexes assigned docs) |

### Response Contract (`200 OK`)
```json
{
  "trie": {
    "c": {
      "u": {
        "r": {
          "r": {
            "$": ["6703b41d24f..."]
          }
        }
      }
    }
  },
  "count": 12
}
```

---

## 🧪 Local Execution & Verification

```python
from backend_v2.functions.search.handler import lambda_handler

event = {
    "version": "2.0",
    "routeKey": "GET /api/v1/search/trie",
    "rawPath": "/api/v1/search/trie",
    "requestContext": {
        "http": {"method": "GET", "path": "/api/v1/search/trie"}
    },
    "headers": {"cookie": "auth_token=..."}
}

response = lambda_handler(event, context=None)
print(response["statusCode"])  # 200
```
