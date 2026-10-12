"""Automated End-to-End Browser Tests for ApexGraph Web Dashboard using Playwright
"""

import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:8000"


def test_dashboard_full_user_flow():
    with sync_playwright() as p:
        # Launch headless browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # 1. Load Overview Dashboard
        page.goto(BASE_URL, wait_until="networkidle")
        assert "ApexGraph" in page.title()

        # Check metric elements are present
        metric_status = page.locator("#metric-status").text_content()
        assert metric_status != ""

        # 2. Navigate to Graph Explorer
        page.locator("button:has-text('Graph Explorer')").click()
        assert page.locator("#graph-canvas").is_visible()

        # 3. Navigate to Symbol Search & Search for 'Calculator'
        page.locator("button:has-text('Symbol Search')").click()
        page.locator("#main-search-input").fill("Calculator")
        page.locator("#btn-search-exec").click()
        page.wait_for_selector("#search-results-tbody tr")

        results_text = page.locator("#search-results-tbody").text_content()
        assert "Calculator" in results_text
        assert "calculator.py" in results_text

        # 4. Navigate to Change Impact & Calculate Blast Radius
        page.locator("button:has-text('Change Impact')").click()
        page.locator("#impact-target-input").fill("add")
        page.locator("#btn-run-impact").click()
        page.wait_for_selector("#impact-results-container", state="visible")

        impact_files = page.locator("#impact-files-list").text_content()
        assert "calculator.py" in impact_files
        assert "math_ops.py" in impact_files

        # 5. Verify Zero Uncaught Console Errors
        # Filter out any favicon or external analytics if any
        actual_errors = [e for e in console_errors if "favicon" not in e]
        assert len(actual_errors) == 0, f"Uncaught console errors detected: {actual_errors}"

        browser.close()
