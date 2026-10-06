# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
#     "Flask>=2.3.0",
#     "pymongo>=4.3.0",
#     "dnspython>=2.3.0",
#     "flask-cors>=3.0.10",
#     "python-dotenv>=1.0.0",
# ]
# ///

"""AWS Serverless Deployment Orchestrator for e-Mailbox."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import get_aws_session, get_mongo_uri
from aws_deploy.helpers.s3_storage import setup_document_bucket
from aws_deploy.helpers.lambda_builder import build_lambda_package
from aws_deploy.helpers.lambda_deployer import deploy_lambda_function
from aws_deploy.helpers.frontend_deployer import deploy_frontend_website


def main():
    print("info: service is running")

    # 1. AWS Session & Database Configuration
    session, account_id, region = get_aws_session()
    mongo_uri = get_mongo_uri()

    # 2. S3 Document Storage & Seed Demo Files
    doc_bucket = setup_document_bucket(session, account_id, region)

    # 3. Build Deployment Package (.zip)
    zip_path = build_lambda_package()

    # 4. Deploy Fat Lambda Backend & Function URL
    function_url = deploy_lambda_function(
        session, account_id, region, doc_bucket, mongo_uri, zip_path
    )

    # 5. Deploy Frontend to S3 Static Website
    website_url = deploy_frontend_website(session, account_id, region, function_url)

    # 6. Deployment Summary
    print("info: deployment complete")
    print(f"info: webpage lives at : {website_url}")
    print(f"info: staff Upload Page : {website_url}/html/staff_upload.html")
    print(f"info: lecturer View Page: {website_url}/html/lecturer_view.html")
    print(f"info: API Gateway Endpoint : {function_url}/api/v1/documents")
    print(f"info: s3 Document Storage : s3://{doc_bucket}")


if __name__ == "__main__":
    main()
