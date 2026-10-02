#!/usr/bin/env python3
"""Build the standalone v1.0 icon set and an optional private app overlay."""
from __future__ import annotations

import argparse
import collections
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from icon_set_style import transform_svg

ROOT = Path(__file__).resolve().parent
IDENTITY = 'WitnessOpsIconsV1_0'
PACKAGE = ROOT / 'packages/icon-set-v1.0' / IDENTITY
ASSETS = ROOT / 'assets/icon-set-v1.0'
NATIVE = Path('/usr/share/icons/breeze-dark')
SPECIAL_APPS = {'utilities-terminal', 'preferences-system', 'systemsettings',
                'org.kde.systemsettings', 'org.kde.dolphin', 'dolphin',
                'system-file-manager', 'konsole', 'org.kde.konsole'}
SIZES = (16, 24, 32, 48, 64, 128, 256)
AUTHORED = {
    'witnessops-ai-cli.png': ('utilities-terminal', 'konsole', 'org.kde.konsole', 'witnessops-ai-cli'),
    'witnessops-blackbox.png': ('witnessops-blackbox',),
    'witnessops-blackbox-browser.png': ('blackbox-browser',),
    'witnessops-credential-import.png': ('witnessops-credential-import',),
    'witnessops-vscodium.png': ('com.vscodium.codium',),
    'witnessops-screenshot.png': ('spectacle',),
    'witnessops-settings.png': ('preferences-system', 'systemsettings', 'org.kde.systemsettings'),
}
AUTHORED_KEYS = frozenset(key for keys in AUTHORED.values() for key in keys)
WIZARD_DESKTOP_ID = 'witnessops-credential-import-wizard.desktop'
WIZARD_ICON_KEY = 'witnessops-credential-import'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_output(path: Path) -> Path:
    path = path.expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Refusing symlink output')
    return path


def prune_empty_icon_directories(text: str, source_root: Path) -> str:
    """Omit empty native directories so the theme survives a Git checkout."""
    index = configparser.ConfigParser(interpolation=None)
    index.optionxform = str
    index.read_string(text)
    removed = set()
    for field in ('Directories', 'ScaledDirectories'):
        kept = []
        for relative in filter(None, index['Icon Theme'].get(field, '').split(',')):
            path = Path(relative)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError('Unsafe native theme directory')
            directory = source_root / path
            populated = directory.is_dir() and any(
                candidate.is_file() and candidate.suffix.lower() in ('.svg', '.svgz', '.png', '.xpm')
                for candidate in directory.iterdir())
            if populated:
                kept.append(relative)
            else:
                removed.add(relative)
        text = re.sub(r'^' + field + r'=.*$', field + '=' + ','.join(kept), text, flags=re.M)
    for relative in removed:
        text = re.sub(r'^\[' + re.escape(relative) + r'\]\n.*?(?=^\[|\Z)', '', text, flags=re.M | re.S)
    return text


def index_text(source: Path, source_root: Path | None = None) -> str:
    # Localized Breeze names would otherwise hide the new identity in settings.
    text = source.read_text()
    text = re.sub(r'^(?:Name|Comment)\[.*?\]=.*\n', '', text, flags=re.M)
    text = re.sub(r'^Name=.*$', 'Name=WitnessOps Icons v1.0', text, flags=re.M)
    text = re.sub(r'^Comment=.*$',
                  'Comment=Individual app identities; graphite, steel and restrained petrol/copper accents',
                  text, flags=re.M)
    text = re.sub(r'^Inherits=.*$', 'Inherits=breeze-dark,hicolor', text, flags=re.M)
    if source_root is not None:
        text = prune_empty_icon_directories(text, source_root)
    # Add explicit fixed sizes for local app identities and authored assets.
    extra = ','.join(f'local/{size}/apps' for size in SIZES)
    text = re.sub(r'^Directories=(.*)$', lambda m: 'Directories=' + extra + ',' + m[1], text, flags=re.M)
    for size in SIZES:
        text += f'\n[local/{size}/apps]\nSize={size}\nType=Fixed\nContext=Applications\n'
    return text


def native_paths(source: Path) -> list[Path]:
    """Follow only the directories explicitly declared by the native theme."""
    index = configparser.ConfigParser(interpolation=None)
    index.read(source / 'index.theme')
    theme = index['Icon Theme']
    directories = theme['Directories'].split(',') + theme.get('ScaledDirectories', '').split(',')
    paths = set()
    for relative in directories:
        if not relative:
            continue
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Unsafe native theme directory')
        directory = source / relative
        if directory.is_dir():
            # Direct glob preserves declared HiDPI aliases without importing
            # nested trees outside the category/size/icon.svg namespace.
            paths.update(directory.glob('*.svg'))
    return sorted(paths)


def build_package(output: Path) -> dict:
    output = safe_output(output)
    if output.exists():
        raise ValueError('Output exists; use a fresh destination')
    output.mkdir(parents=True)
    records = []
    counts = collections.Counter()
    transformed = collections.Counter()
    allowed_sources = (Path('/usr/share/icons/breeze'), NATIVE)
    for path in native_paths(NATIVE):
        source = path.resolve(strict=True)
        if not any(source.is_relative_to(p) for p in allowed_sources):
            raise ValueError('Unexpected Breeze link target: ' + str(path))
        relative = path.relative_to(NATIVE).as_posix()
        data = source.read_bytes()
        styled, changes = transform_svg(data, relative)
        ET.fromstring(styled)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(styled)
        category = relative.split('/')[0]
        counts[category] += 1
        if changes:
            transformed[category] += 1
        records.append({'path': relative, 'source': source.relative_to('/usr/share/icons').as_posix(),
                        'source_sha256': digest(data), 'sha256': digest(styled), 'changes': changes})
    (output / 'index.theme').write_text(index_text(NATIVE / 'index.theme', source_root=NATIVE))
    # Materialize every populated native alias, including HiDPI directories.
    index = configparser.ConfigParser(interpolation=None)
    index.read(output / 'index.theme')
    for key in ('Directories', 'ScaledDirectories'):
        for relative in index['Icon Theme'].get(key, '').split(','):
            if relative:
                (output / relative).mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)
    version = subprocess.check_output(['dpkg-query', '-W', '-f=${Version}',
                                       'kf6-breeze-icon-theme'], text=True).strip()
    license_file = Path('/usr/share/doc/kf6-breeze-icon-theme/copyright')
    (output / 'COPYRIGHT-BREEZE').write_bytes(license_file.read_bytes())
    (output / 'COPYING-BREEZE-ICONS').write_bytes((ROOT / 'licenses/Breeze-COPYING-ICONS').read_bytes())
    summary = {'schema_version': 1, 'version': '1.0.0', 'theme_identity': IDENTITY,
               'basis': 'Complete KDE Breeze Dark; native app identities retained',
               'breeze_package': version,
               'upstream': 'https://invent.kde.org/frameworks/breeze-icons',
               'categories': dict(counts), 'styled_categories': dict(transformed),
               'file_count': len(records), 'records': records,
               'older_witnessops_sets_included': False}
    (ASSETS / 'PROVENANCE.json').write_text(json.dumps(summary, indent=2) + '\n')
    add_folder_aliases(output)
    add_authored_apps(output)
    return summary


def small_native(source: Path, key: str, size: int) -> Path | None:
    """Keep KDE's small SVGs for utilities without an authored replacement."""
    candidates = (size, 22, 16) if size == 24 else (size,)
    aliases = {'konsole': 'utilities-terminal', 'org.kde.konsole': 'utilities-terminal',
               'witnessops-ai-cli': 'utilities-terminal'}
    for native_key in dict.fromkeys((key, aliases.get(key, key))):
        for native_size in candidates:
            candidate = source / f'apps/{native_size}/{native_key}.svg'
            if candidate.is_file():
                return candidate
    return None


def clear_local_variants(destination: Path, key: str, size: int) -> None:
    """Remove conflicting variants only inside the new output being built.

    The private builder copies its publication package first. A previously
    authored small SVG must not remain beside its new PNG and influence lookup.
    Conversely, a native SVG must not be masked by an obsolete copied PNG.
    """
    directory = destination / f'local/{size}/apps'
    for extension in ('.svg', '.svgz', '.png', '.xpm'):
        existing = directory / (key + extension)
        if existing.is_symlink():
            raise ValueError('Refusing symlink local app variant')
        if existing.exists():
            existing.unlink()


def render_apps(source: Path, destination: Path, rows: list[dict], custom: dict[str, Path]) -> list[dict]:
    # Render real supplied/native artwork; never execute desktop commands.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QIcon
    app = QApplication.instance() or QApplication([])
    results = []
    for number, row in enumerate(rows):
        key = row['icon_key']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*', key) or '..' in key:
            raise ValueError('Invalid app icon key')
        original = Path(row['source'])
        if row.get('source_sha256') and digest(original.read_bytes()) != row['source_sha256']:
            raise ValueError('App source artwork changed: ' + key)
        artwork = custom.get(key, original)
        changes = []
        tmp = None
        if key in SPECIAL_APPS and key not in custom and artwork.suffix.lower() == '.svg':
            data, changes = transform_svg(artwork.read_bytes(), 'apps/48/' + key + '.svg')
            # QIcon caches by filename: every source needs a distinct path.
            tmp = destination / f'.render-source-{number}.svg'
            tmp.write_bytes(data)
            artwork = tmp
        icon = QIcon(str(artwork))
        written = []
        for size in SIZES:
            # Published authored icons may be used without their build assets.
            # Preserve those approved PNG bytes at each supplied size, including
            # any future approved small-size alternate. Missing sizes render
            # from the packaged authored source, never from host stock artwork.
            packaged = source / f'local/{size}/apps/{key}.png'
            packaged_data = (packaged.read_bytes()
                             if key in AUTHORED_KEYS and key in custom
                             and artwork.is_relative_to(source / 'local')
                             and packaged.is_file() else None)
            native = (small_native(source, key, size)
                      if key not in custom and key in SPECIAL_APPS and size <= 24 else None)
            clear_local_variants(destination, key, size)
            if packaged_data is not None:
                target = destination / f'local/{size}/apps/{key}.png'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(packaged_data)
                written.append(target.relative_to(destination).as_posix())
                continue
            if native is not None:
                target = destination / f'local/{size}/apps/{key}.svg'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(native.read_bytes())
                written.append(target.relative_to(destination).as_posix())
                continue
            pixmap = icon.pixmap(size, size)
            if pixmap.isNull():
                raise ValueError('App artwork did not render: ' + key)
            target = destination / f'local/{size}/apps/{key}.png'
            target.parent.mkdir(parents=True, exist_ok=True)
            if not pixmap.save(str(target), 'PNG'):
                raise ValueError('App icon write failed: ' + key)
            written.append(target.relative_to(destination).as_posix())
        results.append({'desktop_id': row['desktop_id'], 'icon_key': key,
                        'original_source_sha256': digest(original.read_bytes()),
                        'mode': 'custom asset' if key in custom else ('native utility accents' if changes else 'original app identity'),
                        'paths': written})
        if tmp is not None:
            tmp.unlink()
    return results


def authored_assets() -> dict[str, Path]:
    custom = {}
    for filename, keys in AUTHORED.items():
        path = ASSETS / filename
        if path.is_file():
            custom.update({key: path for key in keys})
    return custom


def add_folder_aliases(package: Path) -> list[dict]:
    records = []
    aliases = {'folder-work': 'folder-documents', 'folder-projects': 'folder-development'}
    for alias, canonical in aliases.items():
        for source in sorted((package / 'places').rglob(canonical + '.svg')):
            target = source.with_name(alias + '.svg')
            if target.exists():
                raise ValueError('Folder alias destination exists: ' + target.relative_to(package).as_posix())
            content = source.read_bytes()
            target.write_bytes(content)
            records.append({'path': target.relative_to(package).as_posix(),
                            'source': source.relative_to(package).as_posix(),
                            'source_sha256': digest(content), 'sha256': digest(content)})
    (ASSETS / 'folder-aliases.json').write_text(json.dumps(
        {'schema_version': 1, 'theme_identity': IDENTITY, 'records': records}, indent=2) + '\n')
    return records


def add_authored_apps(package: Path) -> list[dict]:
    custom = authored_assets()
    rows = [{'desktop_id': key + '.desktop', 'icon_key': key, 'source': str(path)}
            for key, path in custom.items()]
    return render_apps(package, package, rows, custom) if rows else []


def normalize_app_rows(rows: list[dict]) -> list[dict]:
    """Give only the named credential wizard its dedicated theme lookup key."""
    normalized = []
    for original in rows:
        row = dict(original)
        if row['desktop_id'] == WIZARD_DESKTOP_ID:
            if row['icon_key'] not in ('dialog-password', WIZARD_ICON_KEY):
                raise ValueError('Credential import wizard icon key changed')
            row['icon_key'] = WIZARD_ICON_KEY
        normalized.append(row)
    return normalized


def local_package(package: Path, output: Path, inventory: Path) -> dict:
    output = safe_output(output)
    if output.exists():
        raise ValueError('Local output exists; use a fresh destination')
    data = json.loads(inventory.read_text())
    if data['missing_sources'] or data['errors']:
        raise ValueError('App inventory has unresolved source gaps')
    data['apps'] = normalize_app_rows(data['apps'])
    shutil.copytree(package, output)
    custom = authored_assets()
    # An extracted publication package may be used without its build assets.
    # Retain its authored utility icons instead of replacing them with host art.
    for key in sorted(AUTHORED_KEYS):
        if key not in custom:
            for size in reversed(SIZES):
                existing = package / f'local/{size}/apps/{key}.png'
                if existing.is_file():
                    custom[key] = existing
                    break
    for extension in ('.svg', '.png'):
        chatgpt = ROOT / ('.local/icon-set-v1.0-artwork/chatgpt' + extension)
        if chatgpt.is_file():
            custom['chatgpt'] = chatgpt
            break
    results = render_apps(package, output, data['apps'], custom)
    # Theme-local directories are searched first for branded application keys.
    text = (output / 'index.theme').read_text()
    local = ','.join(f'local/{size}/apps' for size in SIZES)
    text = re.sub(r'^Directories=(.*)$', lambda m: 'Directories=' + local + ',' +
                  ','.join(x for x in m[1].split(',') if not x.startswith('local/')), text, flags=re.M)
    (output / 'index.theme').write_text(text)
    coverage = {'schema_version': 1, 'theme_identity': IDENTITY,
                'visible_launchers': len(data['apps']), 'unique_icon_keys': len({r['icon_key'] for r in data['apps']}),
                'apps': results, 'missing_sources': [],
                'absolute_icon_launchers': [{'desktop_id': r['desktop_id'], 'desktop_file': r['desktop_file'],
                                            'icon_key': r['icon_key']} for r in data['apps'] if r['absolute_icon']],
                'publication': 'Host overlay is local only; original app assets are not in the publication package'}
    (output.parent / 'app-coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    return coverage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PACKAGE)
    parser.add_argument('--local-output', type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--local-only', action='store_true')
    args = parser.parse_args()
    if not args.local_only:
        report = build_package(args.output)
        print('Native SVGs:', report['file_count'])
        print('Category coverage:', report['categories'])
        print('Styled variants:', report['styled_categories'])
    if args.local_output:
        if not args.inventory:
            parser.error('--local-output requires --inventory')
        report = local_package(args.output, args.local_output, args.inventory)
        print('Local app coverage:', report['visible_launchers'], 'launchers;', report['unique_icon_keys'], 'identities')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
