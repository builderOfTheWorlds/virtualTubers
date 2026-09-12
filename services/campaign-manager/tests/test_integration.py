
import os
import json
import time
from pathlib import Path
import httpx
import asyncio

# Configuration
BASE_DIR = Path(__file__).resolve().parent
API_URL = "http://localhost:8080"  # Assumes running locally or via docker bridge
REPORT_PATH = BASE_DIR.parent.parent / "campaign_manager_test_report.md"

class TestRunner:
    def __init__(self, api_url):
        self.api_url = api_url
        self.results = []
        self.client = httpx.AsyncClient(base_url=api_url, timeout=30.0)

    async def test_step(self, name, func):
        print(f"Running: {name}...")
        try:
            start_time = time.time()
            await func()
            duration = time.time() - start_time
            self.results.append({"name": name, "status": "✅ PASS", "duration": f"{duration:.2f}s"})
        except Exception as e:
            self.results.append({"name": name, "status": f"❌ FAIL ({str(e)[:50]})", "duration": "N/A"})

    async def test_dashboard_load(self):
        resp = await self.client.get("/")
        if resp.status_code != 200:
            raise Exception(f"Dashboard failed to load: {resp.status_code}")

    async def test_job_submission(self):
        payload = {
            "pack": "ash_test",
            "stage": "arc",
            "profile": "test-profile",
            "segments_json": '["test_seg_1", "test_seg_2"]'
        }
        resp = await self.client.post("/jobs/submit", data=payload)
        if resp.status_code != 303:
             raise Exception(f"Job submission failed: {resp.status_code}")

    async def test_job_retrieval(self):
        jobs_resp = await self.client.get("/jobs")
        jobs = jobs_resp.json()
        if not jobs:
            raise Exception("No jobs found to test retrieval")
        
        job_id = jobs[0]['id']
        detail_resp = await self.client.get(f"/job/{job_id}")
        if detail_resp.status_code != 200:
            raise Exception(f"Job retrieval failed: {detail_resp.status_code}")
        
        if detail_resp.json().get('id') != job_id:
            raise Exception("Job ID mismatch in response")

    async def test_model_url_normalization(self):
        payload = {"model_url": "https://hf.co/bad-url/model-api-tag"}
        resp = await self.client.post("/models/run", data=payload)
        if resp.status_code != 200:
            raise Exception(f"Model run endpoint failed: {resp.status_code}")

    async def write_report(self):
        with open(REPORT_PATH, "w") as f:
            f.write("# Campaign Manager Test Report\n")
            f.write(f"**Date:** {time.ctime()}\n\n")
            f.write("| Test Case | Status | Duration |\n")
            f.write("| :--- | :--- | :--- |\n")
            for r in self.results:
                f.write(f"| {r['name']} | {r['status']} | {r['duration']} |\n")
        print(f"Report written to: {REPORT_PATH}")

async def main():
    tester = TestRunner(API_URL)
    await tester.test_step("Dashboard Load", tester.test_dashboard_load)
    await tester.test_step("Job Submission (Form Data)", tester.test_job_submission)
    await tester.test_step("Job Detailed Retrieval", tester.test_job_retrieval)
    await tester.test_step("Model URL Processing", tester.test_model_url_normalization)
    await tester.write_report()

if __name__ == "__main__":
    asyncio.run(main())
