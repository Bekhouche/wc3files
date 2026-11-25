"""A Layout to and from frame definitions (.fdf), through FdfFile.

What maps between the two: a frame's type, name and template (`INHERITS`),
its size (`Width`, `Height`), its points against another frame
(`SetPoint`), a BACKDROP's texture (`BackdropBackground`), a text
(`Text`) and child frames. A point on the screen has no FDF form, so
to_fdf leaves it to the code (to_jass, to_lua) and says so; a frame made
only from a template has no FDF form either. from_fdf reads those same
properties and lists the ones it leaves out.
"""
from wc3files.fdf.fdf_file import FdfFile, TocFile, Word

from .frames import Frame, Layout, Point, UiError


def to_fdf(layout):
    """(FdfFile, warnings) for the layout's frames."""
    if not isinstance(layout, Layout):
        layout = Layout.from_dict(layout)
    layout.check()
    warnings = []

    def block(frame, parent):
        if not frame.type:
            raise UiError(f'frame {frame.name!r} has only a template; an .fdf frame needs a type')
        items = []
        if frame.size is not None:
            items.append({'kind': 'property', 'name': 'Width', 'values': [frame.size[0]]})
            items.append({'kind': 'property', 'name': 'Height', 'values': [frame.size[1]]})
        for point in frame.points:
            if point.absolute:
                warnings.append(f'frame {frame.name!r}: a screen point has no .fdf form; '
                                f'the code places it')
                continue
            other = parent.name if point.relative_to == 'parent' else point.relative_to
            items.append({'kind': 'property', 'name': 'SetPoint',
                          'values': [Word(point.point), other, Word(point.relative_point),
                                     point.x, point.y]})
        if frame.texture is not None:
            if frame.type == 'BACKDROP':
                items.append({'kind': 'property', 'name': 'BackdropBackground',
                              'values': [frame.texture]})
            else:
                warnings.append(f'frame {frame.name!r}: only a BACKDROP texture has an .fdf '
                                f'form here; the code sets it')
        if frame.text is not None:
            if '"' in frame.text:
                # FdfFile writes a string between quotes as it is, and no
                # escape for a quote inside one is known here.
                warnings.append(f'frame {frame.name!r}: a text with a double quote is not '
                                f'written to the .fdf; the code sets it')
            else:
                items.append({'kind': 'property', 'name': 'Text', 'values': [frame.text]})
        if not frame.visible:
            warnings.append(f'frame {frame.name!r}: visibility has no .fdf form; the code sets it')
        items.extend(block(child, frame) for child in frame.children)
        return {'kind': 'block', 'keyword': 'Frame', 'type': frame.type, 'name': frame.name,
                'inherits': frame.template, 'with_children': False, 'items': items}

    fdf = FdfFile()
    fdf.import_data([block(frame, None) for frame in layout.frames])
    return fdf, warnings


def to_toc(*fdf_paths):
    """A TocFile listing the .fdf files, in game-relative paths."""
    return TocFile().import_data(fdf_paths)


_READ = ('Width', 'Height', 'SetPoint', 'BackdropBackground', 'Text')


def from_fdf(source):
    """(Layout, left_out) from an FdfFile, a path or FDF text. `left_out`
    lists the properties that are not part of the Layout model."""
    fdf = source if isinstance(source, FdfFile) else FdfFile().read(source)
    left_out = set()

    def frame_of(block, parent_name):
        props = {}
        children = []
        points = []
        for item in block['items']:
            if item['kind'] == 'block':
                if item['keyword'] == 'Frame':
                    children.append(frame_of(item, block['name']))
                else:
                    left_out.add(item['keyword'])
            elif item['kind'] == 'property':
                if item['name'] == 'SetPoint':
                    values = item['values']
                    if len(values) != 5:
                        raise UiError(f'frame {block["name"]!r}: SetPoint takes 5 values')
                    point, other, relative, x, y = values
                    points.append(Point(point, x, y,
                                        'parent' if other == parent_name else other, relative))
                elif item['name'] in _READ:
                    props[item['name']] = item['values']
                else:
                    left_out.add(item['name'])
        size = None
        if 'Width' in props and 'Height' in props:
            size = (props['Width'][0], props['Height'][0])
        elif 'Width' in props or 'Height' in props:
            left_out.add('Width' if 'Width' in props else 'Height')
        texture = props.get('BackdropBackground', [None])[0]
        text = props.get('Text', [None])[0]
        return Frame(block['name'], block.get('type'), block.get('inherits'), size, points,
                     None if texture is None else str(texture),
                     None if text is None else str(text), True, children)

    frames = [frame_of(item, None) for item in fdf.items
              if item['kind'] == 'block' and item['keyword'] == 'Frame']
    return Layout(frames), sorted(left_out)
