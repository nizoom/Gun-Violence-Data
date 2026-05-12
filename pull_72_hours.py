from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import sys
import time
import tempfile
import shutil
import logging
import requests
from typing import Optional
from dataclasses import dataclass

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from seleniumbase import Driver


BASE = "https://www.gunviolencearchive.org"
SUB_PAGE = "/last-72-hours"


@dataclass
class ExportConfig:
    """Configuration for CSV export."""
    out_dir: str = "temp"
    prefix: str = "gva_72hr"
    overwrite: bool = False
    timeout: int = 300
    wait_timeout: int = 30


def setup_browser(download_dir: str) -> Driver:
    """Configure SeleniumBase UC driver with download settings."""
    driver = Driver(uc=True, headless=False)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": download_dir,
    })
    return driver


def wait_for_download(download_dir: str, timeout: int = 60) -> Optional[str]:
    """Wait for CSV file to download and return its path."""
    start_time = time.time()
    initial_files = set(os.listdir(download_dir))

    while time.time() - start_time < timeout:
        current_files = set(os.listdir(download_dir))
        new_files = current_files - initial_files

        if new_files:
            for filename in new_files:
                if filename.endswith('.csv') and not filename.endswith('.crdownload'):
                    return os.path.join(download_dir, filename)

        time.sleep(1)

    return None


def export_data(config: ExportConfig, logger: Optional[logging.Logger] = None) -> str:
    """Download GVA 72-hour CSV export."""
    if logger is None:
        logger = logging.getLogger(__name__)

    temp_dir = tempfile.mkdtemp()
    driver = setup_browser(temp_dir)
    wait = WebDriverWait(driver, config.wait_timeout)

    try:
        url = f"{BASE}{SUB_PAGE}"
        logger.info(f"Loading {url}...")

        # UC mode: open with reconnect to handle Cloudflare challenge
        driver.uc_open_with_reconnect(url, 4)
        driver.uc_gui_click_captcha()

        # Human-like delay after Cloudflare challenge
        time.sleep(random.uniform(3, 6))

        logger.info(f"Current URL: {driver.current_url}")
        logger.info(f"Page title: {driver.title}")
        driver.save_screenshot("debug.png")
        logger.info("Screenshot saved to debug.png")

        # Look for export link
        logger.info("Looking for export link...")
        try:
            export_link = wait.until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li a[href*='export-csv']"))
            )
            logger.info(f"Found export link: {export_link.get_attribute('href')}")
        except TimeoutException:
            logger.error(f"Page source: {driver.page_source[:3000]}")
            raise RuntimeError("Could not find export link on page")

        logger.info("Clicking export link...")
        driver.execute_script("arguments[0].click();", export_link)

        # Wait for batch processing to complete and redirect to export-finished
        logger.info("Waiting for export to complete...")
        def is_complete(d):
            return "export-finished" in d.current_url

        try:
            WebDriverWait(driver, config.timeout).until(is_complete)
        except TimeoutException:
            raise RuntimeError(f"Timed out waiting for export after {config.timeout}s")

        logger.info(f"Export finished page reached: {driver.current_url}")

        # Find download link and grab URL
        logger.info("Looking for download link...")
        try:
            download_link = wait.until(
                EC.element_to_be_clickable(
                    (By.CSS_SELECTOR, "a[href*='export-finished/download']")
                )
            )
            download_url = download_link.get_attribute("href")
            logger.info(f"Found download link: {download_url}")
        except TimeoutException:
            logger.error(f"Page source: {driver.page_source[:3000]}")
            raise RuntimeError("Could not find download link on export-finished page")

        # Use requests + browser cookies to download the file directly
        logger.info("Downloading file via requests...")
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        headers = {"User-Agent": driver.execute_script("return navigator.userAgent")}

        response = requests.get(download_url, cookies=cookies, headers=headers, stream=True)
        if response.status_code != 200:
            raise RuntimeError(f"Download request failed with status {response.status_code}")

        os.makedirs(config.out_dir, exist_ok=True)
        target_filename = f"{config.prefix}.csv"
        target_path = os.path.join(config.out_dir, target_filename)

        if os.path.exists(target_path) and not config.overwrite:
            raise ValueError(f"Target file already exists: {target_path}")

        with open(target_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"File saved: {target_path}")
        return target_path

        # Move to target directory
        os.makedirs(config.out_dir, exist_ok=True)
        target_filename = f"{config.prefix}.csv"
        target_path = os.path.join(config.out_dir, target_filename)

        if os.path.exists(target_path) and not config.overwrite:
            raise ValueError(f"Target file already exists: {target_path}")

        shutil.move(downloaded_file, target_path)
        logger.info(f"File saved: {target_path}")
        return target_path

    except Exception as e:
        logger.error(f"Export failed: {e}")
        raise

    finally:
        logger.info("Closing browser...")
        driver.quit()
        try:
            shutil.rmtree(temp_dir)
        except (OSError, FileNotFoundError):
            pass


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    current_year = dt.datetime.now().year
    p = argparse.ArgumentParser(description="Download GVA 72-hour CSV export.")
    p.add_argument("--out-dir", default="temp", help="Output directory (default: temp)")
    p.add_argument("--prefix", default="gva_72hr", help="Output filename prefix (default: gva_72hr)")
    p.add_argument("--timeout", type=int, default=300, help="Export timeout in seconds (default: 300)")
    p.add_argument("--wait-timeout", type=int, default=30, help="WebDriverWait timeout in seconds (default: 30)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite if file already exists")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('gva.log', encoding='utf-8')
        ]
    )
    logger = logging.getLogger(__name__)

    try:
        logger.info("Starting GVA export...")
        config = ExportConfig(
            out_dir=args.out_dir,
            prefix=args.prefix,
            overwrite=args.overwrite,
            timeout=args.timeout,
            wait_timeout=args.wait_timeout,
        )
        result_path = export_data(config, logger)
        logger.info(f"Export complete: {result_path}")
        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 1
    except Exception as e:
        logger.error(f"Export failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())