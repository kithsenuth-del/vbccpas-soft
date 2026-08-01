from pathlib import Path
Path('write_test.txt').write_text('ok', encoding='utf-8')
print('wrote')
