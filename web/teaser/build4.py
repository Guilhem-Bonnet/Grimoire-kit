"""Assemble scene4.html : icônes maison, sorties réelles du terminal, manifeste des rushes, quads d'écran."""
import json
import pathlib
import sys

v4 = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
icons = {p.stem: p.read_text().strip() for p in (v4 / 'icons').glob('*.svg')}
term = json.loads((v4 / 'terminal.json').read_text())
clips = json.loads((v4 / 'clips.manifest.json').read_text())
screens = json.loads((v4 / 'screens.json').read_text()) if (v4 / 'screens.json').exists() else None
tpl = (v4 / 'scene4.tpl.html').read_text()
out = (tpl.replace('/*ICONS*/', 'const ICONS=' + json.dumps(icons, ensure_ascii=False) + ';')
       .replace('/*TERM*/', 'const TERM=' + json.dumps(term, ensure_ascii=False) + ';')
       .replace('/*CLIPS*/', 'const CLIPS=' + json.dumps(clips) + ';' + ('window.SCREENS=' + json.dumps(screens) + ';' if screens else '')))
(v4 / 'scene4.html').write_text(out)
print('ok', len(icons), 'icônes,', len(clips), 'rushes')
