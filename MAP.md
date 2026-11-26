# Source map

| Location | Responsibility |
| --- | --- |
| [launcher](launcher/README.md) | Validate an external manifest, resolve paths and manage the processes it declares. |
| [ruuf.toml](ruuf.toml) | A passive manifest for trying the validator. |
| [kit](kit/README.md) | Build and run a resident from an explicitly selected seed. |
| [routines/ship.py](routines/ship.py) | Run operator-defined checks against a candidate tree, record proof and fast-forward a verified revision. |

Run `python3 launcher/ruufd.py --manifest /path/to/instance/ruuf.toml where` for an instance's own map.
