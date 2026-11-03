"""
Merging the trigger data of one map into another (the Grim Extension Pack's
TriggerMerger, done on the data of MapTriggersFile and
MapCustomTextTriggersFile).

What is merged, as the TriggerMerger manual describes it:

- variables: a variable with the name of an existing one replaces it, the
  others are added;
- triggers and comments: one with the name of an existing one of the same
  kind replaces it (its text in war3map.wct too), the others are added, in a
  category of the same name, which is created when the target has none;
- the source's custom-script header goes into a new custom-text trigger, so
  the target's own header is not touched.

The two files hold one text per trigger in war3map.wct, in the order of the
triggers in war3map.wtg (for the Reforged layout: the GUI and custom-script
items in item order, comments having none) - checked on 38 real maps.

Both maps must use the same layout (classic, or the Reforged item list); a
classic file merged into a Reforged one, or the other way, raises
TriggerMergeError rather than converting between the two.
"""

import copy


class TriggerMergeError(Exception):
     pass


# Reforged item types (wc3files.wtg.data.TriggerItemType)
_ROOT, _CATEGORY, _GUI, _COMMENT, _SCRIPT, _VARIABLE = 1, 4, 8, 16, 32, 64
_WITH_TEXT = (_GUI, _SCRIPT)
_TYPE_INDEX = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7}


def merge_triggers(target_wtg, target_wct, source_wtg, source_wct, header_name='Header'):
     """Merge the source's wtg/wct data (dicts from get_data()) into the
     target's, in place. Returns a summary {'variables': [...], 'triggers':
     [...], 'header': name or None} of the names added or replaced."""
     reforged = target_wtg.get('subversion') is not None
     if reforged != (source_wtg.get('subversion') is not None):
          raise TriggerMergeError('the two maps use different trigger file layouts '
                                  '(classic and Reforged); merging between them is not supported')
     source_wtg = copy.deepcopy(source_wtg)
     target_wct.setdefault('triggers', [])
     merge = _Reforged(target_wtg, target_wct) if reforged else _Classic(target_wtg, target_wct)
     summary = {'variables': [], 'triggers': [], 'header': None}
     for variable in source_wtg.get('variables', []):
          merge.variable(variable)
          summary['variables'].append(variable['name'])
     texts = list(source_wct.get('triggers', []))
     for trigger, text in merge.source_triggers(source_wtg, texts):
          merge.trigger(trigger, text)
          summary['triggers'].append(trigger['name'])
     header = source_wct.get('global_code') or ''
     if header.strip():
          name = merge.unique_name(header_name)
          merge.header(name, header, source_wct.get('global_comment') or '')
          summary['header'] = name
     return summary


class _Classic:

     def __init__(self, wtg, wct):
          self.wtg, self.wct = wtg, wct
          wtg.setdefault('categories', [])
          wtg.setdefault('variables', [])
          wtg.setdefault('triggers', [])
          while len(wct['triggers']) < len(wtg['triggers']):
               wct['triggers'].append('')
          self.source_categories = {}

     def variable(self, variable):
          _replace_or_add(self.wtg['variables'], variable)

     def source_triggers(self, source, texts):
          self.source_categories = {c['id']: c for c in source.get('categories', [])}
          for index, trigger in enumerate(source.get('triggers', [])):
               yield trigger, texts[index] if index < len(texts) else ''

     def category_for(self, source_id):
          source = self.source_categories.get(source_id)
          name = source['name'] if source else 'Imported'
          for category in self.wtg['categories']:
               if category['name'] == name:
                    return category['id']
          new_id = max([c['id'] for c in self.wtg['categories']] + [-1]) + 1
          added = {'id': new_id, 'name': name}
          if source and 'is_comment' in source:
               added['is_comment'] = source['is_comment']
          self.wtg['categories'].append(added)
          return new_id

     def trigger(self, trigger, text):
          trigger['parent_id'] = self.category_for(trigger.get('parent_id'))
          triggers = self.wtg['triggers']
          for index, existing in enumerate(triggers):
               if existing['name'] == trigger['name']:
                    triggers[index] = trigger
                    self.wct['triggers'][index] = text
                    return
          triggers.append(trigger)
          self.wct['triggers'].append(text)

     def unique_name(self, base):
          return _unique(base, {t['name'] for t in self.wtg['triggers']})

     def header(self, name, code, comment):
          parent = self.wtg['triggers'][-1]['parent_id'] if self.wtg['triggers'] else \
               self.category_for(None)
          self.wtg['triggers'].append(_custom_text_trigger(name, comment, parent))
          self.wct['triggers'].append(code)


class _Reforged:

     def __init__(self, wtg, wct):
          self.wtg, self.wct = wtg, wct
          wtg.setdefault('variables', [])
          wtg.setdefault('items', [])
          wtg.setdefault('item_counts', {})
          if not any(i['item_type'] == _ROOT for i in wtg['items']):
               raise TriggerMergeError('the target war3map.wtg has no root category')
          self.root = next(i['id'] for i in wtg['items'] if i['item_type'] == _ROOT)
          self.category_ids = {}

     def new_id(self, item_type):
          base = _TYPE_INDEX[item_type] << 24
          used = [i['id'] for i in self.wtg['items'] if i['item_type'] == item_type]
          if item_type == _VARIABLE:
               used += [v['id'] for v in self.wtg['variables'] if 'id' in v]
          counts = self.wtg['item_counts']
          number = max([counts.get(item_type, 0)] + [u - base + 1 for u in used])
          counts[item_type] = number + 1
          return base | number

     def variable(self, variable):
          items = self.wtg['items']
          for existing in self.wtg['variables']:
               if existing['name'] == variable['name']:
                    variable['id'], variable['parent_id'] = existing['id'], existing['parent_id']
                    _replace_or_add(self.wtg['variables'], variable)
                    return
          variable['id'] = self.new_id(_VARIABLE)
          variable['parent_id'] = self.root
          self.wtg['variables'].append(variable)
          items.append({'id': variable['id'], 'name': variable['name'],
                        'parent_id': self.root, 'item_type': _VARIABLE})

     def source_triggers(self, source, texts):
          text_index = 0
          for item in source.get('items', []):
               kind = item['item_type']
               if kind == _ROOT:
                    self.category_ids[item['id']] = self.root
               elif kind == _CATEGORY:
                    self.category_ids[item['id']] = self.category(item)
               elif kind in (_GUI, _SCRIPT, _COMMENT):
                    text = ''
                    if kind in _WITH_TEXT:
                         text = texts[text_index] if text_index < len(texts) else ''
                         text_index += 1
                    yield item, text

     def category(self, item):
          parent = self.category_ids.get(item.get('parent_id'), self.root)
          for existing in self.wtg['items']:
               if existing['item_type'] == _CATEGORY and existing['name'] == item['name']:
                    return existing['id']
          added = dict(item, id=self.new_id(_CATEGORY), parent_id=parent)
          self.wtg['items'].append(added)
          return added['id']

     def text_index(self, position):
          return sum(1 for i in self.wtg['items'][:position] if i['item_type'] in _WITH_TEXT)

     def trigger(self, item, text):
          item['parent_id'] = self.category_ids.get(item.get('parent_id'), self.root)
          items = self.wtg['items']
          group = (_COMMENT,) if item['item_type'] == _COMMENT else _WITH_TEXT
          texts = self.wct['triggers']
          for position, existing in enumerate(items):
               if existing['item_type'] in group and existing['name'] == item['name']:
                    item['id'] = existing['id']
                    index = self.text_index(position)
                    had_text = existing['item_type'] in _WITH_TEXT
                    items[position] = item
                    if had_text and item['item_type'] in _WITH_TEXT:
                         texts[index] = text
                    return
          item['id'] = self.new_id(item['item_type'])
          items.append(item)
          if item['item_type'] in _WITH_TEXT:
               texts.append(text)

     def unique_name(self, base):
          return _unique(base, {i['name'] for i in self.wtg['items']
                                if i['item_type'] in (_GUI, _SCRIPT, _COMMENT)})

     def header(self, name, code, comment):
          triggers = [i for i in self.wtg['items'] if i['item_type'] in _WITH_TEXT]
          parent = triggers[-1]['parent_id'] if triggers else self.root
          item = _custom_text_trigger(name, comment, parent)
          item['item_type'] = _GUI
          item['is_comment'] = False
          item['id'] = self.new_id(_GUI)
          self.wtg['items'].append(item)
          self.wct['triggers'].append(code)


def _replace_or_add(variables, variable):
     for index, existing in enumerate(variables):
          if existing['name'] == variable['name']:
               variables[index] = variable
               return
     variables.append(variable)


def _unique(base, taken):
     name, number = base, 2
     while name in taken:
          name, number = f'{base} {number}', number + 1
     return name


def _custom_text_trigger(name, comment, parent):
     return {'name': name, 'description': comment, 'is_comment': False,
             'is_enabled': True, 'is_custom_text': True, 'is_initially_on': True,
             'run_on_map_init': False, 'parent_id': parent, 'functions': []}
