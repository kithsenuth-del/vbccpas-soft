"""Check the inline dashboard JavaScript with Node.js."""
import re
import subprocess
from pathlib import Path

html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
if not scripts:
    raise SystemExit("No inline JavaScript was found in index.html")
subprocess.run(["node", "--check"], input=scripts[-1], text=True, check=True)
print("Dashboard JavaScript syntax check passed.")
