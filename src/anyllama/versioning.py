"""Stable release versions shared by the app and release tooling."""

import re


def stable_version(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    try:
        return tuple(map(int, match.groups())) if match else None
    except ValueError:
        return None
