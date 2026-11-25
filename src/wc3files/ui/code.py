"""JASS and Lua that create a Layout's frames with the frame natives
(BlzCreateFrameByType, BlzCreateFrame, BlzFrameSetSize, BlzFrameSetAbsPoint,
BlzFrameSetPoint, BlzFrameSetTexture, BlzFrameSetText, BlzFrameSetVisible),
for Warcraft III 1.31 and later.

The code is one function that creates every frame, in the order of the
layout. In JASS the frames are locals of that function; in Lua they are
also kept in a global table (`UI` by default), by frame name, so other
code can reach them. A frame created from a template needs the .toc that
defines the template loaded first: pass `toc` to have the function load it.
"""
from .frames import Layout, UiError

_GAME_UI = 'BlzGetOriginFrame(ORIGIN_FRAME_GAME_UI, 0)'


def _real(value):
    text = repr(float(value))
    return text[:-2] if text.endswith('.0') else text


def _string(text):
    """A string literal; JASS and Lua escape a backslash and a quote alike."""
    text = str(text)
    if '\n' in text or '\r' in text:
        raise UiError('a frame text or path cannot hold a line break here; use |n')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _steps(layout, ref, string, game_ui):
    """(frame, [statements]) in creation order; `ref(name)` is how the code
    names a frame, `string` quotes a string."""
    if not isinstance(layout, Layout):
        layout = Layout.from_dict(layout)
    layout.check()
    for frame, parent in layout.walk():
        owner = ref(parent.name) if parent is not None else game_ui
        if frame.type:
            create = (f'BlzCreateFrameByType({string(frame.type)}, {string(frame.name)}, '
                      f'{owner}, {string(frame.template or "")}, 0)')
        else:
            create = f'BlzCreateFrame({string(frame.template)}, {owner}, 0, 0)'
        lines = [(None, create)]
        me = ref(frame.name)
        if frame.size is not None:
            lines.append(('call', f'BlzFrameSetSize({me}, {_real(frame.size[0])}, '
                                  f'{_real(frame.size[1])})'))
        for point in frame.points:
            if point.absolute:
                lines.append(('call', f'BlzFrameSetAbsPoint({me}, FRAMEPOINT_{point.point}, '
                                      f'{_real(point.x)}, {_real(point.y)})'))
            else:
                other = ref(parent.name) if point.relative_to == 'parent' else ref(point.relative_to)
                lines.append(('call', f'BlzFrameSetPoint({me}, FRAMEPOINT_{point.point}, {other}, '
                                      f'FRAMEPOINT_{point.relative_point}, '
                                      f'{_real(point.x)}, {_real(point.y)})'))
        if frame.texture is not None:
            lines.append(('call', f'BlzFrameSetTexture({me}, {string(frame.texture)}, 0, true)'))
        if frame.text is not None:
            lines.append(('call', f'BlzFrameSetText({me}, {string(frame.text)})'))
        if not frame.visible:
            lines.append(('call', f'BlzFrameSetVisible({me}, false)'))
        yield frame, lines


def to_jass(layout, function='CreateUI', toc=None):
    """A JASS function `function` that creates the frames."""
    frames = []
    body = []
    if toc:
        body.append(f'    call BlzLoadTOCFile({_string(toc)})')
    for frame, lines in _steps(layout, lambda name: f'ui_{name}', _string, _GAME_UI):
        frames.append(frame.name)
        for kind, line in lines:
            body.append(f'    set ui_{frame.name} = {line}' if kind is None else f'    call {line}')
    locals_ = [f'    local framehandle ui_{name} = null' for name in frames]
    return '\n'.join([f'function {function} takes nothing returns nothing']
                     + locals_ + body + ['endfunction', ''])


def to_lua(layout, function='CreateUI', table='UI', toc=None):
    """A Lua function `function` that creates the frames and keeps them in
    the global table `table`, by name."""
    body = [f'    {table} = {table} or {{}}']
    if toc:
        body.append(f'    BlzLoadTOCFile({_string(toc)})')
    for frame, lines in _steps(layout, lambda name: f'{table}.{name}', _string, _GAME_UI):
        for kind, line in lines:
            body.append(f'    {table}.{frame.name} = {line}' if kind is None else f'    {line}')
    return '\n'.join([f'function {function}()'] + body + ['end', ''])


def init_trigger(code, language, trigger_name, function='CreateUI'):
    """`code` with the InitTrig_<trigger> function the World Editor calls for
    a custom-text trigger of that name. It starts a 0-second timer that
    calls `function`, so the frames are created once the game UI exists."""
    init = 'InitTrig_' + ''.join(c if c.isalnum() else '_' for c in trigger_name)
    if language == 'lua':
        return (code + f'\nfunction {init}()\n'
                f'    TimerStart(CreateTimer(), 0.0, false, {function})\nend\n')
    if language != 'jass':
        raise UiError("language is 'jass' or 'lua'")
    return (code + f'\nfunction {init} takes nothing returns nothing\n'
            f'    call TimerStart(CreateTimer(), 0.0, false, function {function})\n'
            f'endfunction\n')
