# Triggers and scripts

A map's logic exists in two forms, and the difference drives everything here.

- **`war3map.wtg`** - the GUI triggers as the World Editor stores them:
  named triggers, each a tree of events, conditions and actions, with
  parameters and nesting. This is what the editor shows you.
- **`war3map.j`** (or `.lua`) - the script the editor *compiles* those
  triggers into, and what the game actually runs.

A map usually ships both. A map that has been through a protector often ships
only the script.

## Reading the editor's triggers

```python
from wc3files import MapTriggersFile

triggers = MapTriggersFile()
triggers.read('war3map.wtg')

for trigger in triggers.get_triggers():
    print(trigger['name'], len(trigger['functions']), 'functions')
```

Each trigger is a dict: `name`, `description`, `is_enabled`,
`is_initially_on`, `is_comment`, `is_custom_text`, `run_on_map_init`,
`parent_id`, and `functions`. Each function has `type` (0 event, 1 condition,
2 action), `name`, `parameters`, `children` and `branch`.

Nesting lives in `children`, tagged by `branch`: for an `IfThenElseMultiple`,
branch 0 is the conditions, 1 the then-actions, 2 the else-actions. Loops and
enumerations put their body on branch 0.

### Rendering it as text

```python
from wc3files.wtg import trigger_text

lines = trigger_text.describe_trigger(trigger, trigger_text.TriggerText.default())
print('\n'.join(lines))
```

```
Example
    Events:
        Every 2.00 seconds of game time
    Actions:
        Add 1000 experience to (Triggering unit), Hide level-up graphics
```

### When it needs another TriggerData.txt

Reading a `.wtg` requires knowing how many arguments each GUI function takes,
and that lives in the editor's `TriggerData.txt`. The package bundles the
stock one. A map built with an extended editor (WEX and similar) uses
functions stock Warcraft III does not declare, and will raise a `KeyError`
naming the function. Supply that editor's file:

```python
from wc3files.wtg import TriggerData, MapTriggersFile

data = TriggerData()
data.read('path/to/their/TriggerData.txt')

triggers = MapTriggersFile(trigger_data=data)
triggers.read('war3map.wtg')
```

The argument count cannot be guessed - get it wrong and every later parameter
is read as something else - so the library refuses instead.

## Parsing the script

Scripts are lexed and parsed to a real syntax tree, not pattern-matched.

```python
from wc3files import ScriptFile

script = ScriptFile()
script.read('war3map.j')
program = script.get_data()

print(len(program.functions), 'functions')
print(len(program.natives), 'natives')
print(len(program.globals), 'globals')

script.write('formatted.j')          # print it back
```

JASS gives an `ir.Program`; Lua gives an `ir.Chunk`. They share most
expression and statement node types. `get_file_type()` says which you have.

Parse errors carry a position:

```python
from wc3files import JassParseError

try:
    script.read('war3map.j')
except JassParseError as error:
    print(error.line, error.col)
```

JASS-only checking against native and BJ signatures:

```python
for finding in script.validate():
    print(finding['severity'], finding['message'])
```

## Rebuilding GUI triggers from a script

When a map has lost its `war3map.wtg` but kept its script, the triggers can be
reconstructed - the editor's output is regular enough to be read backwards.

```python
from wc3files.wtg.from_script import reconstruct_from_jass

with open('war3map.j', encoding='utf-8', errors='replace') as handle:
    result = reconstruct_from_jass(handle.read())

for trigger in result['triggers']:
    print(trigger['name'], len(trigger['functions']))

print(result['coverage'])
```

The result holds `triggers`, `variables`, `regions`, `cameras`, `sounds` and
a `coverage` summary. The output uses the same shape as a real `.wtg`, so
anything that consumes `MapTriggersFile.get_triggers()` consumes this too -
including `trigger_text.describe_trigger`.

`reconstruct_from_jass` also accepts an already-parsed program, and takes
`trigger_data=` for an extended editor's declarations.

### How good is it

Measured against ground truth rather than estimated: on 299 unprotected maps
that ship *both* a script and the real `war3map.wtg`, the triggers were
rebuilt from the script alone and compared against what the editor had
stored.

| | recovered |
|---|---|
| triggers found | **98.6%** |
| their events, conditions and actions | **93.3%** |
| their parameters | **88.4%** |
| variables | **100%** |

Every GUI trigger was recovered in 288 of the 299 maps.

### What it cannot recover, and why

Some things are simply not in the script to be found:

- **Disabled triggers** are never compiled into it.
- **Custom-text triggers** are hand-written JASS with no GUI structure by
  definition.
- **Empty triggers** emit no initialiser.

Others are genuinely ambiguous, and the library reports what the script says
rather than guessing:

- **`IfThenElse` vs `IfThenElseMultiple`**, and **`ForGroup` vs
  `ForGroupMultiple`** - each pair compiles to an identical call. The
  `Multiple` form is used throughout, which keeps the actions recoverable
  whichever the author actually picked.
- **`SetPlayerUnitMaxAllowed` vs `SetPlayerTechMaxAllowedSwap`** - two
  different GUI functions compiling to the same call, one taking a unit
  rawcode and the other an upgrade rawcode. Telling them apart needs the
  map's object data, which this reader is not given.
- **A comparison whose operand has no declared type** - a bare variable or
  arithmetic - stays raw script. Where the operand *is* typed, the name comes
  back exactly: `GetSpellAbilityId() == 'A000'` can only have been
  `OperatorCompareAbilityId`, because `TriggerData.txt` declares what the call
  returns and which comparison takes that type.

That last point is the general principle. Names are derived from the game's
own declarations where the declarations settle it, and left as script where
they do not. A plausible-looking wrong name is worse than visible raw script,
because it reads as fact.

### Variable names

GUI variables are `udg_`-prefixed in the script and stored without the prefix,
so the prefix is removed. Names of placed objects are a different matter: the
editor turns a region called `ALL HERO` into the identifier `gg_rct_ALL_HERO`,
and that is not reversible - an underscore may have been a space or an
underscore. The identifier is reported as-is rather than guessed back.

## From the command line

```bash
wc3files trigger-text war3map.wtg               # the editor's own wording
wc3files script-to-triggers war3map.j           # rebuild from a script
wc3files script-to-triggers war3map.j --json    # structured output
wc3files jass war3map.j                         # parse and print back
```
