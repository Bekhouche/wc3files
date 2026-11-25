"""In-game UI frames for Warcraft III 1.31 and later (#172).

Describe a UI as a Layout of Frames (or as JSON), then write it as the JASS
or Lua that creates it with the frame natives, as an .fdf of frame
definitions, or as a custom-text trigger in a map's trigger files for World
Editor users. An existing .fdf can be read into a Layout.

    from wc3files.ui import Layout, to_jass
    layout = Layout.from_json(open('ui.json').read())
    print(to_jass(layout))

The model follows what the Reforged UI Designer (RUID) by nightknighto
produces for map makers - code to paste into a trigger - as a library.
"""
from .code import init_trigger, to_jass, to_lua
from .fdf import from_fdf, to_fdf, to_toc
from .frames import POINTS, SCREEN_HEIGHT, SCREEN_WIDTH, Frame, Layout, Point, UiError
from .triggers import add_ui_trigger

__all__ = ['Frame', 'Layout', 'Point', 'UiError', 'POINTS', 'SCREEN_WIDTH', 'SCREEN_HEIGHT',
           'to_jass', 'to_lua', 'init_trigger', 'to_fdf', 'to_toc', 'from_fdf',
           'add_ui_trigger']
