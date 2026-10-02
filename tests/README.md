# Icon-set checks

With the development dependencies in [ICONSET.md](../ICONSET.md):

```sh
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests
python3 pack_icon_set.py
python3 pack_icon_set.py --verify dist/WitnessOps-Icon-Set-v1.0.0.tar.gz
```

Tests cover native paint preservation, app rendering, lookup, inventory, bounded launcher overrides, user-scoped installation/restore and archive integrity. Fixture-based checks run without system Breeze. Checks using the installed native Breeze sources are skipped when those sources are absent; archive provenance still checks the complete committed payload.

Automated checks do not establish every desktop/version's visual compatibility.
