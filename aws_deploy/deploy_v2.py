# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
#     "pymongo>=4.3.0",
#     "dnspython>=2.3.0",
#     "python-dotenv>=1.0.0",
# ]
# ///

"""AWS Serverless Micro-Lambda Deployment Orchestrator for e-Mailbox Backend V2."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import get_aws_session, get_mongo_uri
from aws_deploy.helpers.frontend_deployer import deploy_frontend_website
from aws_deploy.helpers.lambda_deployer import wait_for_lambda_ready
from aws_deploy.helpers.s3_storage import setup_document_bucket

V2_DEPENDENCIES = [
    "pymongo>=4.3.0",
    "dnspython>=2.3.0",
    "python-dotenv>=1.0.0",
]

MICRO_FUNCTIONS = [
    {
        "name": "cs361-g3-v2-documents",
        "handler": "backend_v2.functions.documents.handler.lambda_handler",
        "memory": 256,
        "timeout": 20,
        "routes": [
            "ANY /api/v1/documents",
            "ANY /api/v1/documents/{proxy+}",
        ],
    },
    {
        "name": "cs361-g3-v2-search",
        "handler": "backend_v2.functions.search.handler.lambda_handler",
        "memory": 256,
        "timeout": 15,
        "routes": [
            "ANY /api/v1/search",
            "ANY /api/v1/search/{proxy+}",
        ],
    },
    {
        "name": "cs361-g3-v2-auth",
        "handler": "backend_v2.functions.auth.handler.lambda_handler",
        "memory": 256,
        "timeout": 15,
        "routes": [
            "ANY /api/v1/auth",
            "ANY /api/v1/auth/{proxy+}",
        ],
    },
]


def build_v2_package() -> Path:
    """Bundle Backend V2 code and minimal dependencies into a deployment zip."""
    print("\ninfo: [Step 2] Packaging Backend V2 Micro-Lambda Bundle (.zip)")

    build_dir = PROJECT_ROOT / ".lambda_v2_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    print("   [INFO] Installing V2 dependencies with uv...", end="", flush=True)
    cmd = [
        "uv",
        "pip",
        "install",
        "--python-version",
        "3.11",
        "--python-platform",
        "linux",
        *V2_DEPENDENCIES,
        "--target",
        str(build_dir),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    print(" [Done]")

    print("   [INFO] Copying backend_v2 application code...", end="", flush=True)
    shutil.copytree(PROJECT_ROOT / "backend_v2", build_dir / "backend_v2")
    print(" [Done]")

    zip_path = PROJECT_ROOT / "lambda_v2_package.zip"
    if zip_path.exists():
        zip_path.unlink()

    print("   [INFO] Compressing into zip archive...", end="", flush=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(build_dir):
            for file in files:
                abs_path = Path(root) / file
                archive.write(abs_path, abs_path.relative_to(build_dir))

    shutil.rmtree(build_dir)
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f" [Done] ({size_mb:.2f} MB)")
    return zip_path


def deploy_or_update_lambda(
    lambda_client,
    func_def: dict,
    role_arn: str,
    s3_bucket: str,
    s3_key: str,
    env_vars: dict,
) -> str:
    """Create or update a single micro-Lambda function."""
    name = func_def["name"]
    handler = func_def["handler"]
    memory = func_def["memory"]
    timeout = func_def["timeout"]

    try:
        info = lambda_client.get_function(FunctionName=name)
        arn = info["Configuration"]["FunctionArn"]
        print(f"   [INFO] Updating code for {name}...", end="", flush=True)
        lambda_client.update_function_code(
            FunctionName=name,
            S3Bucket=s3_bucket,
            S3Key=s3_key,
        )
        print(" [Done]")
        wait_for_lambda_ready(lambda_client, name, f"Activating {name}")

        print(f"   [INFO] Updating config for {name}...", end="", flush=True)
        lambda_client.update_function_configuration(
            FunctionName=name,
            Runtime="python3.11",
            Handler=handler,
            Role=role_arn,
            Timeout=timeout,
            MemorySize=memory,
            Environment={"Variables": env_vars},
        )
        print(" [Done]")
        wait_for_lambda_ready(lambda_client, name, f"Applying config for {name}")

    except ClientError as err:
        if err.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"   [INFO] Creating new micro-Lambda: {name}...", end="", flush=True)
            res = lambda_client.create_function(
                FunctionName=name,
                Runtime="python3.11",
                Role=role_arn,
                Handler=handler,
                Code={"S3Bucket": s3_bucket, "S3Key": s3_key},
                Timeout=timeout,
                MemorySize=memory,
                Environment={"Variables": env_vars},
            )
            arn = res["FunctionArn"]
            print(" [Done]")
            wait_for_lambda_ready(lambda_client, name, f"Waiting for {name} to become Active")
        else:
            raise

    return arn


def configure_apigw_v2(
    apigw_client, lambda_client, function_arns: dict, account_id: str, region: str
) -> str:
    """Configure API Gateway HTTP API v2 with path-based routing to micro-Lambdas."""
    api_name = "cs361-g3-v2-api"
    print("\ninfo: [Step 4] Configuring API Gateway HTTP API v2 (Microservice Routes)")

    api_id = None
    api_endpoint = None
    for api in apigw_client.get_apis().get("Items", []):
        if api.get("Name") == api_name:
            api_id = api["ApiId"]
            api_endpoint = api["ApiEndpoint"]
            break

    frontend_url = (
        f"http://cs361-g3-frontend-{account_id}.s3-website-us-east-1.amazonaws.com"
        if region == "us-east-1"
        else f"http://cs361-g3-frontend-{account_id}.s3-website.{region}.amazonaws.com"
    )
    allowed_origins = [frontend_url, "http://localhost:5000", "http://127.0.0.1:5000"]
    custom_origin = os.getenv("FRONTEND_ORIGIN")
    if custom_origin and custom_origin not in allowed_origins:
        allowed_origins.append(custom_origin.rstrip("/"))

    cors_config = {
        "AllowOrigins": allowed_origins,
        "AllowMethods": ["*"],
        "AllowHeaders": ["*"],
        "AllowCredentials": True,
        "MaxAge": 86400,
    }

    if not api_id:
        print("   [INFO] Creating API Gateway HTTP API...", end="", flush=True)
        new_api = apigw_client.create_api(
            Name=api_name,
            ProtocolType="HTTP",
            CorsConfiguration=cors_config,
        )
        api_id = new_api["ApiId"]
        api_endpoint = new_api["ApiEndpoint"]
        print(" [Done]")

        apigw_client.create_stage(ApiId=api_id, StageName="$default", AutoDeploy=True)
    else:
        try:
            apigw_client.update_api(ApiId=api_id, CorsConfiguration=cors_config)
        except ClientError:
            pass

    # Wire routes and integrations for each micro-function
    for func in MICRO_FUNCTIONS:
        name = func["name"]
        arn = function_arns[name]

        print(f"   [INFO] Configuring routes for {name}...", end="", flush=True)
        # Create integration
        integration = apigw_client.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=arn,
            PayloadFormatVersion="2.0",
        )
        integration_id = integration["IntegrationId"]

        # Add routes
        for route_key in func["routes"]:
            try:
                apigw_client.create_route(
                    ApiId=api_id,
                    RouteKey=route_key,
                    Target=f"integrations/{integration_id}",
                )
            except ClientError:
                pass  # Route already exists

        # Allow API Gateway invocation permission
        try:
            lambda_client.add_permission(
                FunctionName=name,
                StatementId=f"ApiGwInvoke-{api_id}-{name}",
                Action="lambda:InvokeFunction",
                Principal="apigateway.amazonaws.com",
                SourceArn=f"arn:aws:execute-api:{region}:{account_id}:{api_id}/*/*",
            )
        except ClientError:
            pass

        print(" [Done]")

    return api_endpoint.rstrip("/")


def main():
    print("info: Backend V2 Serverless Deployment Initiated")

    # 1. AWS Credentials & Storage Setup
    session, account_id, region = get_aws_session()
    mongo_uri = get_mongo_uri()
    role_arn = f"arn:aws:iam::{account_id}:role/LabRole"

    doc_bucket = setup_document_bucket(session, account_id, region)

    # 2. Package V2 Zip
    zip_path = build_v2_package()

    # 3. Upload package to S3
    s3_client = session.client("s3")
    lambda_client = session.client("lambda")
    apigw_client = session.client("apigatewayv2")

    s3_key = "deploy/lambda_v2_package.zip"
    print(f"\ninfo: [Step 3] Uploading bundle to s3://{doc_bucket}/{s3_key}...", end="", flush=True)
    s3_client.upload_file(str(zip_path), doc_bucket, s3_key)
    print(" [Done]")
    if zip_path.exists():
        zip_path.unlink()

    # Environment variables for micro-functions
    env_vars = {
        "MONGO_URI": mongo_uri,
        "S3_BUCKET_NAME": doc_bucket,
        "COGNITO_USER_POOL_ID": os.getenv("COGNITO_USER_POOL_ID", ""),
        "COGNITO_CLIENT_ID": os.getenv("COGNITO_CLIENT_ID", ""),
    }

    # 4. Deploy each Micro-Lambda
    function_arns = {}
    for func in MICRO_FUNCTIONS:
        arn = deploy_or_update_lambda(
            lambda_client=lambda_client,
            func_def=func,
            role_arn=role_arn,
            s3_bucket=doc_bucket,
            s3_key=s3_key,
            env_vars=env_vars,
        )
        function_arns[func["name"]] = arn

    # 5. Configure API Gateway HTTP API v2 Path Routing
    api_endpoint = configure_apigw_v2(
        apigw_client=apigw_client,
        lambda_client=lambda_client,
        function_arns=function_arns,
        account_id=account_id,
        region=region,
    )

    # 6. Deploy Frontend Static Website
    website_url = deploy_frontend_website(session, account_id, region, api_endpoint)

    # 7. Summary
    print("\n" + "=" * 60)
    print("🎉 BACKEND V2 SERVERLESS DEPLOYMENT COMPLETE!")
    print("=" * 60)
    print(f"Website URL       : {website_url}")
    print(f"Staff Upload      : {website_url}/html/staff_upload.html")
    print(f"Lecturer View     : {website_url}/html/lecturer_view.html")
    print(f"Login Page        : {website_url}/html/login.html")
    print(f"API Gateway V2    : {api_endpoint}")
    print("Microservices Active:")
    for f in MICRO_FUNCTIONS:
        print(f"  • {f['name']} ({f['routes']})")
    print(f"S3 Documents      : s3://{doc_bucket}")
    print("=" * 60)


if __name__ == "__main__":
    main()
