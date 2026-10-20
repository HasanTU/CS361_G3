# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "boto3>=1.28.0",
#     "python-dotenv>=1.0.0",
# ]
# ///

"""AWS Cognito User Pool Provisioning Script for e-Mailbox."""

import os
import sys
from pathlib import Path
from botocore.exceptions import ClientError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aws_deploy.helpers.auth import get_aws_session

POOL_NAME = "cs361-g3-userpool"
CLIENT_NAME = "cs361-g3-client"

SEED_USERS = [
    {
        "email": "admin1@tu.ac.th",
        "name": "Kasidit",
        "role": "ADMIN",
        "group": "ADMIN",
        "faculty": "สำนักงานธุรการ",
        "password": "Admin123!",
    },
    {
        "email": "admin2@tu.ac.th",
        "name": "Saowaluk",
        "role": "ADMIN",
        "group": "ADMIN",
        "faculty": "สำนักงานเลขานุการคณะ",
        "password": "Admin123!",
    },
    {
        "email": "user1@tu.ac.th",
        "name": "Prapaporn",
        "role": "USER",
        "group": "USER",
        "faculty": "คณะวิทยาศาสตร์และเทคโนโลยี",
        "password": "User123!",
    },
    {
        "email": "user2@tu.ac.th",
        "name": "Songsakdi",
        "role": "USER",
        "group": "USER",
        "faculty": "คณะศิลปศาสตร์",
        "password": "User123!",
    },
    {
        "email": "user3@tu.ac.th",
        "name": "Wilawan",
        "role": "USER",
        "group": "USER",
        "faculty": "คณะวิศวกรรมศาสตร์",
        "password": "User123!",
    },
]


def get_or_create_user_pool(cognito_client) -> str:
    """Find existing or create a new lightweight Cognito User Pool."""
    print(f"info: Searching for Cognito User Pool '{POOL_NAME}'...", end="", flush=True)
    try:
        pools = cognito_client.list_user_pools(MaxResults=20).get("UserPools", [])
        for p in pools:
            if p["Name"] == POOL_NAME:
                print(f" [Found: {p['Id']}]")
                return p["Id"]

        print(" [Creating New Pool]...", end="", flush=True)
        resp = cognito_client.create_user_pool(
            PoolName=POOL_NAME,
            Policies={
                "PasswordPolicy": {
                    "MinimumLength": 6,
                    "RequireUppercase": False,
                    "RequireLowercase": False,
                    "RequireNumbers": True,
                    "RequireSymbols": False,
                }
            },
            AutoVerifiedAttributes=["email"],
            UsernameAttributes=["email"],
            MfaConfiguration="OFF",
        )
        pool_id = resp["UserPool"]["Id"]
        print(f" [Created: {pool_id}]")
        return pool_id
    except ClientError as err:
        print(f"\nerror: Failed to create or find user pool: {err}")
        sys.exit(1)


def get_or_create_app_client(cognito_client, pool_id: str) -> str:
    """Find existing or create an App Client with USER_PASSWORD_AUTH."""
    print(f"info: Searching for App Client '{CLIENT_NAME}'...", end="", flush=True)
    try:
        clients = cognito_client.list_user_pool_clients(
            UserPoolId=pool_id, MaxResults=20
        ).get("UserPoolClients", [])
        for c in clients:
            if c["ClientName"] == CLIENT_NAME:
                print(f" [Found: {c['ClientId']}]")
                return c["ClientId"]

        print(" [Creating New App Client]...", end="", flush=True)
        resp = cognito_client.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=CLIENT_NAME,
            GenerateSecret=False,
            ExplicitAuthFlows=[
                "ALLOW_USER_PASSWORD_AUTH",
                "ALLOW_REFRESH_TOKEN_AUTH",
                "ALLOW_USER_SRP_AUTH",
            ],
            PreventUserExistenceErrors="ENABLED",
        )
        client_id = resp["UserPoolClient"]["ClientId"]
        print(f" [Created: {client_id}]")
        return client_id
    except ClientError as err:
        print(f"\nerror: Failed to create app client: {err}")
        sys.exit(1)


def ensure_groups(cognito_client, pool_id: str):
    """Ensure ADMIN and USER groups exist in the User Pool."""
    for group_name in ["ADMIN", "USER"]:
        try:
            cognito_client.create_group(
                GroupName=group_name,
                UserPoolId=pool_id,
                Description=f"Application {group_name} role group",
            )
            print(f"info: Created group '{group_name}'")
        except cognito_client.exceptions.GroupExistsException:
            pass
        except ClientError as err:
            if err.response["Error"]["Code"] != "GroupExistsException":
                print(f"warn: Group {group_name} warning: {err}")


def seed_test_users(cognito_client, pool_id: str):
    """Seed pre-confirmed test users into Cognito."""
    print("\ninfo: Seeding test accounts in Cognito...")
    for user in SEED_USERS:
        email = user["email"]
        password = user["password"]
        group = user["group"]
        name = user["name"]

        try:
            cognito_client.admin_create_user(
                UserPoolId=pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                    {"Name": "name", "Value": name},
                ],
                MessageAction="SUPPRESS",
            )
            print(f" - Created user: {email}")
        except ClientError as err:
            if err.response["Error"]["Code"] == "UsernameExistsException":
                print(f" - User already exists: {email}")
            else:
                print(f" - Error creating {email}: {err}")

        # Set permanent password so user does not need first-time password reset
        try:
            cognito_client.admin_set_user_password(
                UserPoolId=pool_id,
                Username=email,
                Password=password,
                Permanent=True,
            )
        except Exception as err:
            print(f"   Password setup warning for {email}: {err}")

        # Add to group
        try:
            cognito_client.admin_add_user_to_group(
                UserPoolId=pool_id,
                Username=email,
                GroupName=group,
            )
        except Exception:
            pass


def update_env_file(pool_id: str, client_id: str):
    """Update or append Cognito variables in .env."""
    env_path = PROJECT_ROOT / ".env"
    lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    keys_set = set()
    new_lines = []
    for line in lines:
        if line.strip().startswith("COGNITO_USER_POOL_ID="):
            new_lines.append(f"COGNITO_USER_POOL_ID={pool_id}\n")
            keys_set.add("COGNITO_USER_POOL_ID")
        elif line.strip().startswith("COGNITO_CLIENT_ID="):
            new_lines.append(f"COGNITO_CLIENT_ID={client_id}\n")
            keys_set.add("COGNITO_CLIENT_ID")
        else:
            new_lines.append(line)

    if "COGNITO_USER_POOL_ID" not in keys_set:
        new_lines.append(f"\nCOGNITO_USER_POOL_ID={pool_id}\n")
    if "COGNITO_CLIENT_ID" not in keys_set:
        new_lines.append(f"COGNITO_CLIENT_ID={client_id}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print(f"\ninfo: Updated .env with COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID.")


def main():
    print("============================================================")
    print("🚀 AWS COGNITO USER POOL SETUP (LEAN & COST-FREE)")
    print("============================================================")
    session, account_id, region = get_aws_session()
    cognito_client = session.client("cognito-idp", region_name=region)

    pool_id = get_or_create_user_pool(cognito_client)
    client_id = get_or_create_app_client(cognito_client, pool_id)

    ensure_groups(cognito_client, pool_id)
    seed_test_users(cognito_client, pool_id)
    update_env_file(pool_id, client_id)

    print("\n============================================================")
    print("🎉 COGNITO SETUP COMPLETE!")
    print(f"User Pool ID : {pool_id}")
    print(f"App Client ID: {client_id}")
    print("Test Users:")
    for u in SEED_USERS:
        print(f" - {u['email']} | Password: {u['password']} | Group: {u['group']}")
    print("============================================================")


if __name__ == "__main__":
    main()
