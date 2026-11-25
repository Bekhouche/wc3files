# In-game UI frames

Warcraft III 1.31 and later let a map create its own UI frames with the
frame natives (`BlzCreateFrameByType`, `BlzFrameSetPoint`, ...).
`wc3files.ui` describes such a UI as data and writes the code that creates
it, as the [Reforged UI Designer (RUID)](https://github.com/nightknighto/Warcraft-3-Reforged-UI-Designer)
does for map makers, but from Python or the command line.

## A layout

A layout is a list of frames. Each frame has a name, a type (an FDF frame
type such as `BACKDROP`, `TEXT` or `GLUETEXTBUTTON`) or a template to be
created from, and optionally a size, anchor points, a texture, a text, a
visibility and child frames. Sizes and positions are in screen units: the
4:3 area is 0.8 wide and 0.6 high, with 0, 0 at the bottom left.

```json
{
  "frames": [
    {
      "name": "Panel",
      "type": "BACKDROP",
      "size": [0.24, 0.12],
      "points": [{"point": "CENTER", "x": 0.4, "y": 0.3}],
      "texture": "UI\\Widgets\\EscMenu\\Human\\blank-background.blp",
      "children": [
        {"name": "Title", "type": "TEXT", "text": "Hello",
         "points": [{"point": "TOP", "relative_to": "parent", "relative_point": "TOP",
                     "x": 0, "y": -0.01}]}
      ]
    }
  ]
}
```

A point without `relative_to` is placed on the screen
(`BlzFrameSetAbsPoint`); with it, against `"parent"` or another frame by
name (`BlzFrameSetPoint`). `point` and `relative_point` are one of
`TOPLEFT`, `TOP`, `TOPRIGHT`, `LEFT`, `CENTER`, `RIGHT`, `BOTTOMLEFT`,
`BOTTOM`, `BOTTOMRIGHT`. A frame is created after its parent, and can be
placed only against frames created before it.

A frame with a `type` is created with `BlzCreateFrameByType`, inheriting
its `template` if it has one; a frame with only a `template` with
`BlzCreateFrame`. A template that is not one of the game's own needs its
.toc loaded first: pass `toc`, and the code calls `BlzLoadTOCFile`.

`Layout.check()` refuses what cannot be created (a name used twice or not
an identifier, a frame with neither a type nor a template, an unknown
point, a point against a frame that does not exist yet) and returns
warnings for screen points outside the 0.8 x 0.6 area.

## From the command line

```bash
wc3files ui-build ui.json                       # the JASS, to stdout
wc3files ui-build ui.json --jass ui.j --lua ui.lua --fdf ui.fdf
wc3files ui-build frames.fdf --json ui.json     # read a layout from an .fdf
wc3files ui-build ui.json --map MyMap.w3x --out MyMap.ui.w3x
```

`--map` adds a custom-text trigger (`UI` by default, `--trigger-name`) to
the map's `war3map.wtg` and `war3map.wct`, in JASS or Lua as the map's
`war3map.w3i` says. It shows in the World Editor's trigger editor, and its
`InitTrig_` function creates the frames on a 0-second timer, so they are
made once the game UI exists. The map's own script (`war3map.j` or
`war3map.lua`) is not changed: the trigger runs once the map is saved in
the World Editor. A protected map is refused.

## From Python

```python
from wc3files.ui import Layout, to_jass, to_lua, to_fdf, from_fdf, add_ui_trigger

layout = Layout.from_json(open('ui.json', encoding='utf-8').read())
print(to_jass(layout, function='CreateUI'))   # locals, in creation order
print(to_lua(layout, table='UI'))             # UI.Panel, UI.Title, ...
fdf, warnings = to_fdf(layout)                # an FdfFile, and what it left out
layout2, left_out = from_fdf('frames.fdf')    # and what it did not read
```

`add_ui_trigger(wtg, wct, layout, trigger_name='UI', language='jass')` merges
the trigger into `MapTriggersFile` and `MapCustomTextTriggersFile` data
(`get_data()`), classic or Reforged layout, through `wtg.merge.merge_triggers`.

## What the .fdf carries

`to_fdf` and `from_fdf` map a frame's type, name and template
(`INHERITS`), its size (`Width`, `Height`), its points against other frames
(`SetPoint`), a `BACKDROP`'s texture (`BackdropBackground`), its text
(`Text`) and its children. A point on the screen, a visibility, a texture
on another frame type, and a text with a double quote have no .fdf form
here; `to_fdf` reports each one, and the code still sets them. `from_fdf`
lists the properties it does not read.

The generated JASS and Lua are checked by the package's own JASS and Lua
checkers against the game's `common.j`. They have not been run in the game
by the tests.
