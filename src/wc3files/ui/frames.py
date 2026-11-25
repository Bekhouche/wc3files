"""A small model of in-game UI frames (#172), the frames Warcraft III 1.31
and later create with BlzCreateFrameByType and BlzCreateFrame.

A `Layout` is a list of top-level `Frame`s. A frame has a name, a type (an
FDF frame type such as BACKDROP, TEXT or GLUETEXTBUTTON) or a template it
is created from, a size and anchor points in screen units (the 4:3 area is
0.8 wide and 0.6 high, with 0, 0 at the bottom left), and optionally a
texture, a text, a visibility and child frames.

    {"frames": [
      {"name": "Panel", "type": "BACKDROP", "size": [0.2, 0.1],
       "points": [{"point": "CENTER", "x": 0.4, "y": 0.3}],
       "texture": "UI\\\\Widgets\\\\EscMenu\\\\Human\\\\blank-background.blp",
       "children": [
         {"name": "Title", "type": "TEXT", "text": "Hello",
          "points": [{"point": "TOP", "relative_to": "parent",
                      "relative_point": "TOP", "x": 0, "y": -0.01}]}]}]}

A point without `relative_to` is placed on the screen; one with it is
placed against another frame (`"parent"`, or a frame's name).
"""
import json
import re

POINTS = ('TOPLEFT', 'TOP', 'TOPRIGHT', 'LEFT', 'CENTER', 'RIGHT',
          'BOTTOMLEFT', 'BOTTOM', 'BOTTOMRIGHT')
SCREEN_WIDTH = 0.8
SCREEN_HEIGHT = 0.6
_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class UiError(ValueError):
    """A layout that cannot be turned into frames."""


class Point(object):
    __slots__ = ('point', 'x', 'y', 'relative_to', 'relative_point')

    def __init__(self, point, x=0.0, y=0.0, relative_to=None, relative_point=None):
        self.point = str(point).upper()
        self.x = float(x)
        self.y = float(y)
        self.relative_to = relative_to
        self.relative_point = (str(relative_point).upper() if relative_point
                               else (self.point if relative_to else None))

    @property
    def absolute(self):
        return self.relative_to is None

    def to_dict(self):
        out = {'point': self.point, 'x': self.x, 'y': self.y}
        if not self.absolute:
            out['relative_to'] = self.relative_to
            out['relative_point'] = self.relative_point
        return out

    @classmethod
    def from_dict(cls, data):
        return cls(data['point'], data.get('x', 0.0), data.get('y', 0.0),
                   data.get('relative_to'), data.get('relative_point'))

    def __eq__(self, other):
        return isinstance(other, Point) and self.to_dict() == other.to_dict()

    def __repr__(self):
        return f'Point({self.to_dict()!r})'


class Frame(object):
    __slots__ = ('name', 'type', 'template', 'size', 'points', 'texture', 'text',
                 'visible', 'children')

    def __init__(self, name, type=None, template=None, size=None, points=None,
                 texture=None, text=None, visible=True, children=None):
        self.name = name
        self.type = type.upper() if type else None
        self.template = template
        self.size = tuple(float(v) for v in size) if size is not None else None
        self.points = list(points or [])
        self.texture = texture
        self.text = text
        self.visible = bool(visible)
        self.children = list(children or [])

    def to_dict(self):
        out = {'name': self.name}
        for key in ('type', 'template'):
            if getattr(self, key):
                out[key] = getattr(self, key)
        if self.size is not None:
            out['size'] = list(self.size)
        if self.points:
            out['points'] = [p.to_dict() for p in self.points]
        for key in ('texture', 'text'):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if not self.visible:
            out['visible'] = False
        if self.children:
            out['children'] = [c.to_dict() for c in self.children]
        return out

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or 'name' not in data:
            raise UiError(f'a frame needs a name: {data!r}')
        return cls(data['name'], data.get('type'), data.get('template'), data.get('size'),
                   [Point.from_dict(p) for p in data.get('points') or []],
                   data.get('texture'), data.get('text'), data.get('visible', True),
                   [cls.from_dict(c) for c in data.get('children') or []])

    def __eq__(self, other):
        return isinstance(other, Frame) and self.to_dict() == other.to_dict()

    def __repr__(self):
        return f'Frame({self.name!r})'


class Layout(object):
    """The frames of one UI, in creation order."""

    def __init__(self, frames=None):
        self.frames = list(frames or [])

    def walk(self):
        """(frame, parent frame or None) for every frame, parents first."""
        stack = [(frame, None) for frame in reversed(self.frames)]
        while stack:
            frame, parent = stack.pop()
            yield frame, parent
            stack.extend((child, frame) for child in reversed(frame.children))

    def to_dict(self):
        return {'frames': [f.to_dict() for f in self.frames]}

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, list):
            data = {'frames': data}
        if not isinstance(data, dict) or not isinstance(data.get('frames'), list):
            raise UiError('a layout is {"frames": [...]}')
        layout = cls([Frame.from_dict(f) for f in data['frames']])
        layout.check()
        return layout

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text):
        return cls.from_dict(json.loads(text))

    def check(self):
        """Raise UiError for what the game cannot create: a name that is not
        an identifier or appears twice, a frame with neither a type nor a
        template, an unknown anchor point, or a point relative to a frame
        that is not created before it. Returns warnings (a list of str) for
        screen points outside the 0.8 x 0.6 area, which are legal but
        usually a mistake."""
        warnings = []
        seen = set()
        for frame, parent in self.walk():
            if not isinstance(frame.name, str) or not _NAME.match(frame.name):
                raise UiError(f'frame name {frame.name!r} must be letters, digits and '
                              f'underscores, not starting with a digit')
            if frame.name in seen:
                raise UiError(f'two frames are named {frame.name!r}')
            if not frame.type and not frame.template:
                raise UiError(f'frame {frame.name!r} needs a type (such as BACKDROP or '
                              f'TEXT) or a template')
            if frame.size is not None and len(frame.size) != 2:
                raise UiError(f'frame {frame.name!r}: size is [width, height]')
            for point in frame.points:
                for name in (point.point, point.relative_point):
                    if name is not None and name not in POINTS:
                        raise UiError(f'frame {frame.name!r}: unknown point {name!r}, '
                                      f'one of {", ".join(POINTS)}')
                if point.relative_to == 'parent':
                    if parent is None:
                        raise UiError(f'frame {frame.name!r} is placed against its parent '
                                      f'but is a top-level frame')
                elif point.relative_to is not None and point.relative_to not in seen:
                    raise UiError(f'frame {frame.name!r} is placed against '
                                  f'{point.relative_to!r}, which is not created before it')
                if point.absolute and not (0 <= point.x <= SCREEN_WIDTH
                                           and 0 <= point.y <= SCREEN_HEIGHT):
                    warnings.append(f'frame {frame.name!r}: screen point {point.x}, {point.y} '
                                    f'is outside the 0.8 x 0.6 area')
            seen.add(frame.name)
        return warnings
