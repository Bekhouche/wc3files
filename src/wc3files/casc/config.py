"""The plain-text layer of a CASC install: `.build.info` and the build/CDN
configs it points at.

This is the entry point for everything else - it is what turns an install
directory into the hashes the binary layers need (where the encoding table
is, where the root is, which archives exist).

All three files are text, so unlike the rest of this package's binary
formats there is nothing to guess at here.
"""
import os


class CascConfigError(Exception):
    pass


def parse_build_info(path):
    """Parse `.build.info` - a pipe-delimited table with a typed header row
    (`Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|...`).

    Returns a list of row dicts keyed by the plain column name, with the
    `!TYPE:SIZE` suffix stripped.
    """
    with open(path, 'r', encoding='utf-8') as handle:
        lines = [line.rstrip('\r\n') for line in handle if line.strip()]
    if not lines:
        raise CascConfigError(f'{path} is empty')

    columns = [field.split('!', 1)[0].strip() for field in lines[0].split('|')]
    rows = []
    for line in lines[1:]:
        values = line.split('|')
        # A trailing empty column is normal; pad rather than reject the row.
        if len(values) < len(columns):
            values += [''] * (len(columns) - len(values))
        rows.append(dict(zip(columns, values)))
    return rows


def active_build(path):
    """The row of `.build.info` for the build this install currently has
    active, or the only row if none is explicitly marked."""
    rows = parse_build_info(path)
    active = [row for row in rows if row.get('Active') == '1']
    if len(active) == 1:
        return active[0]
    if not rows:
        raise CascConfigError(f'{path} lists no builds')
    if len(active) > 1:
        raise CascConfigError(
            f'{path} marks {len(active)} builds active; expected exactly one'
        )
    if len(rows) == 1:
        return rows[0]
    raise CascConfigError(
        f'{path} lists {len(rows)} builds and marks none active'
    )


def parse_config(path):
    """Parse a build or CDN config: `key = value` lines, `#` comments.

    Values are kept as a list of whitespace-separated tokens, because that is
    what they are - `encoding = <content key> <encoded key>` and
    `archives = <hash> <hash> ...` are both one key with several values, and
    collapsing them to a string would just mean re-splitting at every use.
    """
    config = {}
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            config[key.strip()] = value.split()
    return config


def config_path(data_dir, key):
    """Where a config hash lives on disk: `config/<first 2>/<next 2>/<hash>`."""
    if len(key) < 4:
        raise CascConfigError(f'config key {key!r} is too short to form a path')
    return os.path.join(data_dir, 'config', key[0:2], key[2:4], key)
