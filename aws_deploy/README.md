# AWS Serverless Deployment Guide (AWS Learner Lab)

This folder (`aws_deploy/`) contains all the tools and configurations to deploy the **e-Mailbox** system to **AWS Learner Lab** without touching or modifying any of the original project files.

---

## System Architecture

```text
+--------------------------------------------------------------------+
|                       User / Lecturer / Staff                      |
+--------------------------------------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------+
|  1. Amazon S3 (Static Website Hosting)                             |
|     Bucket: cs361-g3-frontend-<ACCOUNT_ID>                         |
|     - Hosts: staff_upload.html, lecturer_view.html, CSS, JS        |
+--------------------------------------------------------------------+
                                   |
                      (AJAX / Fetch API Requests)
                                   v
+--------------------------------------------------------------------+
|  2. AWS API Gateway (HTTP API v2)                                  |
|     Endpoint: https://<id>.execute-api.us-east-1.amazonaws.com     |
|     - Routes public traffic with permissive CORS                   |
+--------------------------------------------------------------------+
                                   |
                       (AWS_PROXY Integration)
                                   v
+--------------------------------------------------------------------+
|  3. AWS Lambda (Fat Lambda Backend)                                |
|     Function: cs361-g3-backend                                     |
|     - Runs the Flask application                                   |
|     - Executes under IAM Role: LabRole                             |
+--------------------------------------------------------------------+
                   /                              \
                  /                                \
                 v                                  v
+-------------------------------+  +---------------------------------+
|  4. Amazon S3 (Documents)     |  |  5. MongoDB Atlas (Cloud NoSQL) |
|     Bucket:                   |  |     Database: e_mailbox         |
|     cs361-g3-documents-<ID>   |  |     Collections:                |
|     - Stores uploaded PDFs &  |  |     - documents                 |
|       Images                  |  |     - document_types            |
+-------------------------------+  +---------------------------------+
```

---

## Prerequisites

### 1. AWS Learner Lab Credentials
In your **AWS Learner Lab** console:
1. Click **"AWS Details"**.
2. Next to **AWS CLI**, click **"Show"**.
3. Copy the credentials block (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`).
4. In your `.env` file, paste and configure them:
   ```env
   AWS_DEFAULT_REGION=us-east-1
   AWS_ACCESS_KEY_ID=ASI...
   AWS_SECRET_ACCESS_KEY=...
   AWS_SESSION_TOKEN=...
   ```

### 2. MongoDB Atlas Connection String
Ensure you have a MongoDB connection string in `.env`:
```env
MONGO_URI=mongodb+srv://<username>:<password>@cluster0.xxxxx.mongodb.net/e_mailbox?retryWrites=true&w=majority
```
*(Make sure MongoDB Atlas **Network Access** allows `0.0.0.0/0` so AWS Lambda can connect).*

---

## How to Deploy (Single Command)

Using `uv`, run the deployment script:

```bash
uv run aws_deploy/deploy.py
```

The script will automatically:
1. Connect to AWS STS and discover your `LabRole` & Account ID.
2. Create the **S3 Document Bucket** and upload sample seed PDFs.
3. Build the lightweight Lambda deployment package with all Python dependencies.
4. Deploy/Update the **Fat Lambda** function with memory 512MB and 30s timeout.
5. Provision the **AWS API Gateway HTTP API** with public CORS.
6. Create the **S3 Frontend Bucket**, configure Static Website Hosting and public read policy.
7. Inject the API Gateway endpoint into the frontend files and upload them to S3.
8. Output the live public website URLs!

---

## Grading & Presentation Walkthrough

When presenting to your instructor or grader, you can demonstrate the following:

### 1. Web Application Live Demo
- Open the **Staff Upload Page** (`http://cs361-g3-frontend-<id>.s3-website-us-east-1.amazonaws.com/html/staff_upload.html`).
- Click **"Fill Valid Form"** -> **"บันทึกเอกสาร"** -> Notice the instant success toast.
- Open the **Lecturer View Page** (`.../html/lecturer_view.html`).
- Click **"ดูตัวอย่างเอกสาร"** (Eye icon) -> See the PDF render inside the modal.
- Click the download icon to download the document.

### 2. AWS Console Proof
- **S3 Console**:
  - `cs361-g3-frontend-<id>`: Show static website hosting enabled and the uploaded HTML files.
  - `cs361-g3-documents-<id>`: Show the `uploads/` folder containing the uploaded PDFs.
- **API Gateway Console**:
  - Show `cs361-g3-api` HTTP API routing `$default` to Lambda with CORS.
- **Lambda Console**:
  - Open `cs361-g3-backend`.
  - Show **Environment Variables** (`MONGO_URI`, `S3_BUCKET_NAME`).
  - Show **CloudWatch Logs** (under the "Monitor" tab) to prove live API invocations.
- **MongoDB Atlas Console**:
  - Show the `e_mailbox` database with `documents` collection containing your registered metadata.

---

## How to Teardown (Clean Up)

Whenever you want to completely delete all cloud resources:

```bash
uv run aws_deploy/destroy.py
```
Type `yes` when prompted to cleanly remove API Gateway, Lambda, and both S3 buckets.
