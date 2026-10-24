"""
Bundled real icon-border art (classic 64x64 and Reforged 256x256 styles), used
as ready-made defaults for `make-icon`/`make-icon-set`'s `--border`-family flags
so a caller isn't required to supply their own border image just to get a
real, in-game-style result.

**Provenance**: copied from a real, locally-available third-party
icon-authoring tool ("Warcraft Image Lab"), with explicit authorization from
its owner to copy and use these specific asset files in this package. No
separate open-source license file was found alongside them, so - like every
other bundled real-asset directory in this package (see `w3o/gamedata/`,
`scripts/gamedata/`'s own provenance notes) - they're included on that
explicit authorization, not under a verified public license.

**Not a universal ground truth** - checked directly, not assumed: this
border art does *not* exactly reproduce a real, independently-produced
benchmark icon set's own borders (see TODO.md's `composite_over`
benchmark-verification entry, which found byte-exact matches using each real
icon set's *own* border art, but a few hundred pixels of difference against
*this* bundled art on the same icon). Border style genuinely varies map to
map / mod to mod in practice - this is one real, usable style, not "the"
correct one, which is exactly why every `--border`-family CLI flag still
takes a caller-supplied override in preference to these bundled defaults.
"""
import os

_BORDERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gamedata', 'borders')

# icon kind -> bundled border filename. Classic style is 64x64 (matches the
# classic tutorial's Icon_Border.TGA convention); Reforged style is 256x256
# (matches the Reforged tutorial's higher-resolution border set) and needs
# resizing down to the target icon size before compositing (resize_box, the
# same downscale tool used everywhere else in this package for exactly this).
CLASSIC_BORDERS = {
     'active': 'Icon_Border.png',
     'passive': 'Icon_Border_Passive.png',
     'autocast': 'Icon_Border_Autocast.png',
     'upgrade': 'Icon_Border_Attack_Upgrade.png',
     'attack': 'Icon_Border_Attack.png',
     'disabled': 'Icon_Border_Disabled.png',
}

REFORGED_BORDERS = {
     'active': 'Reforged_Icon_Border_Button.png',
     'passive': 'Reforged_Icon_Border_Passive.png',
     'autocast': 'Reforged_Icon_Border_Autocast.png',
     'upgrade': 'Reforged_Icon_Border_Attack_Upgrade.png',
     'attack': 'Reforged_Icon_Border_Attack.png',
     'disabled': 'Reforged_Icon_Border_Disabled.png',
     # Reforged-only: a distinct disabled-passive border (no classic-style
     # equivalent was found bundled with the source tool).
     'passive_disabled': 'Reforged_Icon_Border_Passive_Disabled.png',
}


def border_path(kind, reforged=False):
     """Absolute path to the bundled default border image for `kind` - one of
     'active', 'passive', 'autocast', 'upgrade', 'attack', 'disabled', or
     (Reforged-only) 'passive_disabled'. Raises KeyError for an unknown kind."""
     table = REFORGED_BORDERS if reforged else CLASSIC_BORDERS
     return os.path.join(_BORDERS_DIR, table[kind])
