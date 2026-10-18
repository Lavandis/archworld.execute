#!/usr/bin/env python3
"""Create an allowlisted release archive with soundtrack, excluding unrelated project files."""
import hashlib
from pathlib import Path
import tarfile

root = Path(__file__).resolve().parents[1]
version = '0.1.0'
name = f'archworld.execute-{version}'
out = root / 'dist'
out.mkdir(exist_ok=True)
archive = out / f'{name}.tar.gz'
files = ['world.execute', 'src/player.py', 'data/frames.jsonl.gz',
         'Makefile', 'README.md', 'NOTICE.md', 'LICENSE',
         'tests/test_player.py', 'tests/smoke.py', 'packaging/PKGBUILD.local',
         'packaging/prepare.py']
music = root / 'audio/music.mp3'
if not music.is_file():
    music = root.parent / 'public/audio/world.execute(me).mp3'
if not music.is_file():
    raise SystemExit(f'Missing soundtrack: {music}')
for relative in files:
    if not (root / relative).is_file():
        raise SystemExit(f'Missing distribution input: {relative}')
with tarfile.open(archive, 'w:gz') as tar:
    for relative in files:
        tar.add(root / relative, arcname=f'{name}/{relative}', recursive=False)
    alias = tarfile.TarInfo(f'{name}/archworld.execute')
    alias.type = tarfile.SYMTYPE
    alias.linkname = 'world.execute'
    alias.mode = 0o755
    tar.addfile(alias)
    tar.add(music, arcname=f'{name}/audio/music.mp3', recursive=False)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
recipe = (root / 'packaging/PKGBUILD.local').read_text()
(out / 'PKGBUILD').write_text(recipe.replace('REPLACE_WITH_ARCHIVE_SHA256', digest))
print(f'Release archive: {archive}\nSHA256: {digest}\nSee NOTICE.md for soundtrack and artwork attribution.')
