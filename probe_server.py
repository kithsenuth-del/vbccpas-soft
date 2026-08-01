import json
import urllib.request
from pathlib import Path

url = 'http://127.0.0.1:8000/api/members'
path = Path('probe_server.txt')
try:
    with urllib.request.urlopen(url, timeout=5) as response:
        body = response.read().decode('utf-8')
        path.write_text(f'STATUS={response.status}\n{body}', encoding='utf-8')
except Exception as exc:
    path.write_text(f'ERROR={type(exc).__name__}: {exc}', encoding='utf-8')
