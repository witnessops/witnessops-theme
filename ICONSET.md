# WitnessOps Icon Set v1.0

The separate theme identity is `WitnessOpsIconsV1_0`. It combines the complete
installed KDE Breeze Dark icon family with the selected **Machined Carbon**
direction: graphite surfaces, steel edges and restrained petrol accents. Native
folder shapes and file-type marks remain recognizable. BLACK BOX uses a cube,
WitnessOps AI CLI a connected viewport, and Settings a gear. BLACK BOX Browser,
Credential Import Wizard, VSCodium and Spectacle have separate authored artwork. Small
functional color details serve these distinct designs. Each of these seven
application icons is supplied as a PNG at 16, 24, 32, 48, 64, 128 and 256 pixels, so small
sizes use the same selected identity. Other native symbolic utilities retain
their editable SVGs and `currentColor` contrast behavior.

Home and the folder types share graphite fronts and steel outlines. Named color
variants retain their identifying color as a rear accent. Their native folder
silhouettes and type glyphs continue to distinguish their purpose.

`assets/icon-set-v1.0/PROVENANCE.json` records the native source package, source
hashes, output hashes, categories and paint changes. Its file count includes
size variants and aliases, rather than counting unique designs. The selected
WitnessOps artwork, exact source hashes and rendered size hashes are in
`assets/icon-set-v1.0/artwork.json`.
`assets/icon-set-v1.0/folder-aliases.json` ties the additional Work and Projects
folder names to their exact styled native Documents and Development SVGs.
These names are reserved for alias records; they cannot be classified as native
source records. Published alias files require this manifest and must match their
recorded source bytes.

## Requirements and desktop support

Python 3.10 or later is required. Installation, checksum verification and archive
packaging use the Python standard library. Building PNG variants and private
overlays requires PyQt5 with Qt's SVG plugin; preview boards additionally require
Pillow. On Debian/Ubuntu these optional development dependencies are
`python3-pyqt5`, `python3-pyqt5.qtsvg` and `python3-pil`.

The files use the freedesktop icon-theme format. The included activation command
targets an active Plasma 6 session with `kwriteconfig6`; other desktops can select
the installed icon set through their own appearance settings. Compatibility with
every desktop/version has not been tested.

## Install and verify

From an extracted archive, check its complete checksum manifest first:

```sh
sha256sum -c SHA256SUMS
```

Then run these commands as the desktop user from either the checkout or archive:

```sh
python3 install_icon_set.py plan
python3 install_icon_set.py install --yes
python3 install_icon_set.py verify
```

The installer copies this separate set to the user's icon directory and changes
KDE's `Icons.Theme` selection. Activation requires an active Plasma desktop
session and `kwriteconfig6`. It records the previous selection and exact payload
hashes in a private receipt. Existing differing files are refused, and older
WitnessOps sets remain available.

For a rehearsal that leaves the selected theme unchanged:

```sh
python3 install_icon_set.py install --yes --no-activate
python3 install_icon_set.py verify --no-activate
```

Use `--data-home` and `--config-home` together to rehearse in temporary user-owned
directories. Installation does not change wallpapers, panel layout, terminal
profiles, shell configuration, login screens or boot components.

## Roll back

Use the receipt printed by the installation command:

```sh
python3 install_icon_set.py restore --backup /path/to/icon-set-v1.0-RECEIPT.json
```

For a receipt created with `--no-activate`, add `--no-activate` to restoration.
Restore removes only files created by that installation and restores its prior
icon selection when applicable. It refuses edited owned files or an unrelated
later icon selection. Retain the source archive and receipt until restoration
is complete.

## Original application identities on a machine

The distributable set inherits `breeze-dark,hicolor`, so applications can use
their installed original artwork. Third-party application logos gathered from
a workstation are kept in a private overlay rather than redistributed in this
archive. Absolute-path launcher icons bypass theme lookup and require a
separately receipted user launcher override when one is explicitly intended.

To prepare a fresh private overlay in a repository checkout:

```sh
python3 tools/inventory_icons.py --output .local/app-inventory.json
python3 build_icon_set.py --local-only \
  --local-output .local/icon-set-overlay/WitnessOpsIconsV1_0 \
  --inventory .local/app-inventory.json
python3 install_icon_set.py plan \
  --source .local/icon-set-overlay/WitnessOpsIconsV1_0
```

The overlay renderer requires PyQt5. Installing that overlay is a separate
explicit operation using the same `--source` argument. Its inventory, original
app assets, launcher backups and receipts remain private. The included
`tools/launcher_icon_overrides.py` has two bounded scopes. Its default
`absolute-icons` scope is limited to `blackbox-browser.desktop`,
`com.yubico.yubioath.desktop` and `tuxedo-control-center.desktop`. The separate `credential-import` scope changes only
`witnessops-credential-import-wizard.desktop`, mapping its existing
`dialog-password` key to `witnessops-credential-import` in a backed-up user
launcher. The shared password icon remains native. Launcher commands and other
fields are preserved. Each scope has its own private verification and rollback
receipt. Install and restore refuse root or elevated execution and require
user-owned data directories, launcher destinations and receipt/backup paths.
System launcher files may be read as sources; changes are written only to the
user-owned destinations. Temporary user-owned directories are supported through
`--data-home`.

Plan the named wizard override against a fresh private inventory:

```sh
python3 tools/launcher_icon_overrides.py plan \
  --inventory .local/app-inventory.json --scope credential-import
```

Its installation is an explicit `install --yes` operation with those same
arguments. Use the resulting receipt with `verify --backup RECEIPT` or
`restore --yes --backup RECEIPT`. These launcher receipts are separate from the
icon-theme installation receipt.

The included `tools/preview_icon_set.py` can render diagnostic boards from an
explicit theme directory and a private inventory. Its `--help` lists the current
preview options. A preview is generated evidence of those selected assets;
checking the active desktop remains a separate installation verification.

## Source, licensing and packaging

The committed native basis is KDE Breeze Icons package `6.24.0-0ubuntu1~tux1`.
The exact per-file hashes in `PROVENANCE.json` identify this payload. Rebuilding
against another system Breeze version can change the artwork and aliases; it
does not reproduce the committed v1.0 bytes. Use the committed package when
creating its standalone archive. `build_icon_set.py --output NEW_DIRECTORY`
creates a fresh native derivative from `/usr/share/icons/breeze-dark` and
requires the corresponding system package copyright file. It refuses to
overwrite an existing output directory. Native builds enumerate only direct SVG
files in the source theme's declared directories, including HiDPI aliases. The
committed v1.0 baseline also retains recorded upstream variants that the source
index does not advertise; those original artifact bytes are preserved.

The native source is KDE Breeze Icons. Breeze artwork and derivatives retain
their applicable artwork licenses, including the exceptions recorded in the
included `COPYRIGHT-BREEZE`; the repository's Apache-2.0 license does not replace
those licenses. The Breeze artwork license is included under
`licenses/Breeze-COPYING-ICONS` and inside the theme directory. Complete
GPL-3.0, LGPL-2.1 and CC-BY-SA-4.0 texts accompany the package in `licenses/`.
The native Skladnik SVG retains its own CC-BY-SA-4.0 notice and attribution.
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the source and scope.
Third-party app names and marks remain the property of their respective owners.

The reproducible standalone archive is built with:

```sh
python3 -m unittest discover -s tests -p 'test_icon*.py' -v
python3 -m unittest discover -s tests -p 'test_launcher_icon_overrides.py' -v
python3 pack_icon_set.py
```

The packer verifies per-file native provenance, the seven selected artwork source
hashes, and every authored PNG's rendered hash and size. It rejects incomplete
size coverage and competing local SVG variants. It materializes regular files
and directories, writes a complete internal `SHA256SUMS`, and verifies the archive
without extracting it. It uses an explicit allowlist containing only this
v1.0 set, its selected artwork, implementation, relevant tests, documentation and
licenses. Only the fixed native category/size SVG subtrees and selected app PNGs
are accepted; every SVG also requires matching provenance. The archive verifier
requires the mandatory scripts, licenses, theme index and artwork, then checks
the same provenance and authored-size rules against the bytes it reads. Both
native manifests are mandatory and their SHA256 digests are pinned in the v1
packer, binding verification to all 50,201 native SVGs and 22 Work/Projects
aliases. Rewriting manifests and checksums cannot authorize a reduced or
substituted native set. A deliberate new baseline requires reviewing changes
to the pinned digests together with its icon inventory; even manifest
reformatting changes those digests. The canonical artwork manifest is also
pinned, binding the seven selected sources and all 84 rendered PNGs to their
approved bytes. A
self-consistent checksum manifest alone is insufficient. Third-party notices
come directly from the required `THIRD_PARTY_NOTICES.md` source file. The archive
`README.md` is this document.

Archive verification hashes input and file bodies in chunks of at most 64 KiB.
It permits at most 60,000 entries, 120,000 headers (including local PAX path
extensions), 128 MiB compressed input, 256 MiB decompressed tar data and
128 MiB of file payload. Each file is limited to 32 MiB, the checksum manifest
to 16 MiB, small JSON metadata to 1 MiB and the theme index to 256 KiB.
Local PAX headers are capped at 8 KiB and may only extend a path once; sparse,
global and other extension types are excluded before their payload is parsed.
Only zero padding is allowed after the tar end marker.
Only bounded metadata stays in memory during verification. Package collection
still snapshots the trusted source payload in memory.

The standalone archive excludes earlier theme sets, host overlays, inventories,
receipts, caches and Git metadata. The package builder operates locally; it does not publish a release or push Git commits.
