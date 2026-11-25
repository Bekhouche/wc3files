"""Put a Layout's code into a map's triggers, for World Editor users (#172).

The code goes into a custom-text trigger, merged into the map's
war3map.wtg and war3map.wct with wtg.merge.merge_triggers, so it shows in
the trigger editor and is part of the script the editor writes the next
time the map is saved. Classic and Reforged trigger files are both
handled. The trigger's InitTrig_ function starts the frames on a 0-second
timer (code.init_trigger).
"""
from wc3files.wtg.merge import merge_triggers

from .code import init_trigger, to_jass, to_lua
from .frames import UiError

# Reforged trigger item types (wtg.merge, TriggerItemType).
_ROOT, _CATEGORY, _GUI, _SCRIPT = 1, 4, 8, 32
# Marks a trigger this module made, so only such a trigger is replaced.
_COMMENT = 'Created by wc3files: the frames of a UI layout.'


def _trigger(name, comment, parent):
    return {'name': name, 'description': comment, 'is_comment': False,
            'is_enabled': True, 'is_custom_text': True, 'is_initially_on': True,
            'run_on_map_init': False, 'parent_id': parent, 'functions': []}


def add_ui_trigger(wtg, wct, layout, trigger_name='UI', category='UI', language='jass',
                   function='CreateUI', toc=None):
    """Merge a custom-text trigger `trigger_name` that creates `layout` into
    the war3map.wtg and war3map.wct data (dicts from get_data()), in place.
    `language` is 'jass' or 'lua', the map's script language (war3map.w3i
    `script_type`; the trigger files do not say). A trigger of the same name
    that this function made before is replaced; any other trigger of that
    name raises UiError, so a map's own trigger is never overwritten.
    Returns merge_triggers' summary."""
    make = to_lua if language == 'lua' else to_jass
    code = init_trigger(make(layout, function=function, toc=toc), language,
                        trigger_name, function)
    comment = _COMMENT
    existing = (wtg.get('items') if wtg.get('subversion') is not None else wtg.get('triggers')) or []
    for item in existing:
        if (item.get('name') == trigger_name and item.get('item_type', _GUI) in (_GUI, _SCRIPT)
                and item.get('description') != _COMMENT):
            raise UiError(f'the map already has a trigger named {trigger_name!r} that wc3files '
                          f'did not make; choose another name (--trigger-name)')
    if wtg.get('subversion') is not None:
        source = {'subversion': wtg['subversion'], 'variables': [], 'items': [
            {'id': 0, 'name': 'root', 'is_comment': False, 'is_expanded': True,
             'parent_id': -1, 'item_type': _ROOT},
            {'id': 1, 'name': category, 'is_comment': False, 'is_expanded': True,
             'parent_id': 0, 'item_type': _CATEGORY},
            dict(_trigger(trigger_name, comment, 1), item_type=_SCRIPT, id=2),
        ]}
    else:
        source = {'variables': [], 'categories': [{'id': 0, 'name': category, 'is_comment': False}],
                  'triggers': [_trigger(trigger_name, comment, 0)]}
    return merge_triggers(wtg, wct, source, {'triggers': [code]})
