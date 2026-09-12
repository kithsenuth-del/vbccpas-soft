from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parent
out = root / 'vendor' / 'tailwind.min.js'
out.parent.mkdir(parents=True, exist_ok=True)
url = 'https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4/dist/index.global.js'
with urllib.request.urlopen(url, timeout=30) as response:
    data = response.read()
out.write_bytes(data)
print(f'exists={out.exists()}')
print(f'size={out.stat().st_size}')
