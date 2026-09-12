"""Behavioral test for the explicitly requested offline +/- counter fixture."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True)
    parser.add_argument("--screenshot", required=True)
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    application = Path(args.app).resolve()
    screenshot = Path(args.screenshot).resolve()
    assert application.is_file()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=args.browser_executable)
        try:
            context = browser.new_context(offline=True, viewport={"width": 1000, "height": 720})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(application.as_uri())
            assert page.locator("#count").inner_text() == "0"
            page.get_by_role("button", name="+", exact=True).click()
            assert page.locator("#count").inner_text() == "1"
            page.get_by_role("button", name="-", exact=True).click()
            assert page.locator("#count").inner_text() == "0"
            page.get_by_role("button", name="-", exact=True).click()
            assert page.locator("#count").inner_text() == "-1"
            assert not errors, errors
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot))
            print(json.dumps({"status": "passed", "checks": ["initial-zero", "plus", "minus", "negative-count", "no-js-errors", "offline"], "screenshot": str(screenshot)}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
