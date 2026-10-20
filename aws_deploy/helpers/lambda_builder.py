"""Package builder for AWS Lambda deployment zip."""

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Python runtime dependencies (boto3 is already built into AWS Lambda runtime)
LAMBDA_DEPENDENCIES = [
    "Flask>=2.3.0",
    "pymongo>=4.3.0",
    "dnspython>=2.3.0",
    "flask-cors>=3.0.10",
    "python-dotenv>=1.0.0",
]


def build_lambda_package() -> Path:
    """Bundle dependencies and code into a lightweight deployment zip using uv.

    Returns:
        Path: Path to the generated lambda_package.zip file.
    """
    print("\ninfo: [Step 2] Packaging Lambda Deployment Bundle (.zip)")

    build_dir = PROJECT_ROOT / ".lambda_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    print("   [INFO] Installing packages with uv...", end="", flush=True)
    cmd = [
        "uv",
        "pip",
        "install",
        "--python-version",
        "3.11",
        "--python-platform",
        "linux",
        *LAMBDA_DEPENDENCIES,
        "--target",
        str(build_dir),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    print(" [Done]")

    print("   [INFO] Copying application code...", end="", flush=True)
    shutil.copytree(PROJECT_ROOT / "backend", build_dir / "backend")
    shutil.copytree(PROJECT_ROOT / "aws_deploy", build_dir / "aws_deploy")

    uploads_dir = PROJECT_ROOT / "uploads"
    if uploads_dir.exists():
        shutil.copytree(uploads_dir, build_dir / "uploads")
    print(" [Done]")

    zip_path = PROJECT_ROOT / "lambda_package.zip"
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
    print(f"info: Lambda package created: lambda_package.zip ({size_mb:.2f} MB)")
    return zip_path
