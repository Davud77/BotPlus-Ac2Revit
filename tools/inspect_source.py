from pathlib import Path
import sys

root = Path('research/tapir-source-1.7/archicad-addon/Sources')
if len(sys.argv) > 2:
    text = (root / sys.argv[1]).read_text(encoding='utf-8')
    for key in sys.argv[2:]:
        at = text.find(key)
        print(key, text[at:at+6000] if at >= 0 else 'NOT FOUND')
