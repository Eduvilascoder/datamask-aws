"""Despliega el build del frontend a Amplify (manual deploy)."""
import subprocess
import json
import zipfile
import os
import urllib.request

APP = "d3nxh77gcdsvjs"
BRANCH = "main"
PROFILE = "masterGenAI"
REGION = "us-east-1"
BUILD_DIR = "/Users/eduvilas/Documents/eduthecoder/DataMask-AWS/frontend/build"
ZIP = "/tmp/amplify_clean.zip"


def aws(*args):
    return subprocess.run(
        ["aws", *args, "--profile", PROFILE, "--region", REGION],
        capture_output=True, text=True,
    )


# Stop any pending/running job
res = aws("amplify", "list-jobs", "--app-id", APP, "--branch-name", BRANCH,
          "--max-results", "5")
jobs = json.loads(res.stdout) if res.stdout else {}
for j in jobs.get("jobSummaries", []):
    if j["status"] in ("PENDING", "RUNNING", "PROVISIONING"):
        aws("amplify", "stop-job", "--app-id", APP, "--branch-name", BRANCH,
            "--job-id", j["jobId"])
        print("stopped", j["jobId"])

# Build zip
with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, _dirs, files in os.walk(BUILD_DIR):
        for f in files:
            fp = os.path.join(root, f)
            zf.write(fp, os.path.relpath(fp, BUILD_DIR))
print("zip ready")

# Create deployment
res = aws("amplify", "create-deployment", "--app-id", APP, "--branch-name", BRANCH)
dep = json.loads(res.stdout)
job_id = dep["jobId"]
url = dep["zipUploadUrl"]
print("job", job_id)

# Upload via PUT
with open(ZIP, "rb") as fh:
    data = fh.read()
req = urllib.request.Request(
    url, data=data, method="PUT",
    headers={"Content-Type": "application/zip"},
)
with urllib.request.urlopen(req) as resp:
    print("upload status", resp.status)

# Start deployment
res = aws("amplify", "start-deployment", "--app-id", APP, "--branch-name", BRANCH,
          "--job-id", job_id)
print(res.stdout)
print("JOB_ID=" + job_id)
