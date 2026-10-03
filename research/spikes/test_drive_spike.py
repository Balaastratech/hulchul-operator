"""Spike S4: Google Drive export URLs (Sheet->CSV, Doc->text, PDF) and write capability probe."""

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import requests


def probe_public_drive_folder(folder_id: str) -> dict[str, str]:
    """Scrape file IDs and names from a public 'anyone with link' Google Drive folder."""
    url = f"https://drive.google.com/drive/folders/{folder_id}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    resp = requests.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return {"error": f"Failed to fetch folder: HTTP {resp.status_code}"}

    # Extract embedded JSON data in the public folder page
    # Drive embeds drive item metadata in javascript data arrays
    html = resp.text
    files: dict[str, str] = {}
    # Search for patterns of file IDs and file names
    # e.g., ["1A2B3C...", "profile", ...]
    matches = re.findall(r'\["([a-zA-Z0-9_-]{25,})",\s*\["([^"]+)"', html)
    for fid, fname in matches:
        files[fname] = fid
    return files


def fetch_sheet_csv(file_id: str) -> tuple[bool, str, str]:
    """Fetch public Google Sheet as CSV via export URL."""
    url = f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            content = resp.text
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            return True, content, h
        return False, f"HTTP {resp.status_code}", ""
    except Exception as e:
        return False, str(e), ""


def fetch_doc_text(file_id: str) -> tuple[bool, str, str]:
    """Fetch public Google Doc as plain text via export URL."""
    url = f"https://docs.google.com/document/d/{file_id}/export?format=txt"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            content = resp.text
            h = hashlib.sha256(content.encode("utf-8")).hexdigest()
            return True, content, h
        return False, f"HTTP {resp.status_code}", ""
    except Exception as e:
        return False, str(e), ""


def fetch_drive_file(file_id: str) -> tuple[bool, bytes, str]:
    """Fetch public Drive file (e.g. PDF) via direct download URL."""
    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            content = resp.content
            h = hashlib.sha256(content).hexdigest()
            return True, content, h
        return False, b"", f"HTTP {resp.status_code}"
    except Exception as e:
        return False, b"", str(e)


def probe_unauthenticated_sheet_write(file_id: str) -> tuple[bool, str]:
    """Test if writing a row to an 'anyone with link can edit' Google Sheet is possible via HTTP without credentials."""
    # Attempt 1: POST to spreadsheet URL
    url = f"https://docs.google.com/spreadsheets/d/{file_id}/values/A1:append"
    try:
        resp = requests.post(url, json={"values": [["test_val"]]}, timeout=10)
        if resp.status_code == 200:
            return True, "Unexpected HTTP 200 on unauthenticated write"
        return False, f"Rejected: HTTP {resp.status_code} ({resp.reason})"
    except Exception as e:
        return False, f"Failed: {e}"


if __name__ == "__main__":
    print("Testing unauthenticated row write probe on dummy sheet ID...")
    ok, reason = probe_unauthenticated_sheet_write("dummy_sheet_id_12345")
    print(f"Unauthenticated write possible without credentials: {ok} ({reason})")
