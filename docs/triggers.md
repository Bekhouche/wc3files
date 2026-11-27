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

### YDWE's ui folder

An extended editor (YDWE and the plugins built on it) keeps its GUI
functions not in `TriggerData.txt` but in a `ui` folder: `define.txt` with
the type and category sections, and `event.txt`, `condition.txt`,
`action.txt`, `call.txt` with one section per function (title,
description, category, script name, arguments). `trigger-ui` converts
between the two, as w3x2lni's ui-builder does:

```bash
wc3files trigger-ui TriggerData.txt TriggerStrings.txt ui/   # editor files -> ui folder
wc3files trigger-ui ui/ plugin1/ plugin2/ out/               # ui folders, merged -> editor files
wc3files trigger-ui ui/ out/ --lang zhCN                     # with a language's text
```

```python
from wc3files.wtg import ydwe_ui

ui = ydwe_ui.read_trigger_data(data_text, strings_text)
ydwe_ui.write_ui(ui, 'ui')
merged = ydwe_ui.merge(ydwe_ui.read_ui('ui'), ydwe_ui.read_ui('plugin'))
data_text, strings_text = ydwe_ui.write_trigger_data(merged)
```

A later folder's function or type replaces an earlier one of the same name.
Nothing is dropped either way: each function's version number and AI flags
are kept, as is a stray line with no declaration. On the bundled Reforged
files (1,448 functions), both directions give back the same definitions.

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

## Checking a script

`validate()` checks a script against the game API in the bundled `common.j`
and `blizzard.j`, for JASS and for Reforged's `war3map.lua`:

```python
script = ScriptFile().read('war3map.lua')
for finding in script.validate():
    print(finding.get('line'), finding['severity'], finding['message'])

script.write('checked.lua', validate=True)   # raises ValueError on an issue
```

From the command line: `wc3files jass war3map.lua --check`.

A finding is an `issue` when the code is wrong and a `note` when it is worth
a look. The checker only reports what it is sure of; anything it cannot
resolve is skipped.

For JASS it reports calls to unknown functions, wrong argument counts and
arguments of an incompatible type, each with the line of its statement.

Lua has no declarations, so the Lua checker works from what the script shows:

1. **Names.** A global the script calls must be a native, a `blizzard.j`
   function, a Lua 5.3 library function, `FourCC`, or something the script
   assigns somewhere, in any order and in any function. Anything else fails
   when it runs with "attempt to call a nil value". A global that is read but
   never assigned is a note. Names that Lua 5.1 had and 5.3 does not
   (`unpack`, `math.pow`, ...) and natives that only `common.ai` declares
   (`UnitAlive`) are notes too.
2. **Library members.** `math.flor(x)` is an issue, unless the script adds
   `flor` to `math` itself (`function math.flor(...)`).
3. **Game calls.** A call to a native or a `blizzard.j` function is checked
   for its argument count and for the argument types that can be told
   without running the code: literals, results of other game calls,
   functions, and locals that are assigned once. The ones that matter most:
   - a string where an object id is expected, `CreateUnit(p, 'hfoo', ...)`
     instead of `FourCC('hfoo')`;
   - a function where a boolexpr is expected, `TriggerAddCondition(t, f)`
     instead of `Condition(f)`;
   - a handle of the wrong type, such as a trigger passed to `KillUnit`;
   - the result of a function that returns nothing.

   Missing arguments whose parameters are handles are a note, since Lua
   passes nil, which is null for a handle.

4. **Tables and functions the script defines.** A table made by a
   constructor (`local M = {}`, `Lib = {}`) whose keys are all set by name
   (`M.x = ...`, `function M:Update()`) is checked: a call to a key it never
   sets (`M:Updtae()`) is an issue, a read a note. A table that could gain
   keys out of sight (handed to a function, given a metatable, aliased,
   assigned again, written with a computed key) is not checked. A call to a
   script function with more arguments than it declares is a note: Lua drops
   the extra ones.

What it leaves alone, because it is valid Lua:

- locals that shadow a native, and natives the script replaces with its own
  function (a hook);
- feature tests: a name read in a condition, in `and`/`or`, in a comparison
  or in `type(...)`, such as `table.unpack or unpack` or `if Debug then`.
  Such a name is then optional everywhere in the script;
- operators other than comparisons, whose result is only inferred for plain
  numbers and strings, since a metatable can overload them;
- conversions Lua does itself: a number where a string is expected, and a
  numeric string where a number is expected.

A script that writes globals by computed name (`_G[k] = v`,
`rawset(_G, ...)`, `setmetatable(_G, ...)`), or loads code (`load`,
`require`, `dofile`), can define any name, so it gets one note saying so and
no undefined-name findings.

Members can also be added to a library through a local alias
(`local Math = math; Math.Abs = abs`, as the CSharp.lua runtime does), or by
a function it is passed to; the checker follows both. Only reading functions
such as `pairs` leave it alone.

On the 36 real `war3map.lua` scripts of the development machine (generated,
hand-written, TypeScript and C# transpiled, obfuscated), it reports issues in
8, all genuine: `BlzFrameSetVertexColor(frame, 255, 204, 0, 220)` in six
copies of one map (the native takes one packed color, so the color passed is
255), and a call to a `testChars` that is defined nowhere in two others.

### Handle leaks

For JASS, `jass --check` also reports handle leaks, as warnings: handles
that are created and never destroyed. A location, group or effect lives
until the script destroys it, so a trigger that makes one on every run and
drops it slows the game down over a long match.

```python
from wc3files.scripts import parse_jass
from wc3files.scripts.leaks import find_leaks

for finding in find_leaks(parse_jass(text)):
    print(finding['line'], finding['message'])
```

Three cases are reported:

- a new handle dropped at once, passed to a function that does not keep it
  or called as a statement: `call CreateNUnitsAtLoc(1, 'hfoo', p,
  GetRectCenter(r), 0)` or `call AddSpecialEffect(...)`;
- a local set to a new handle and, in the same function, never destroyed,
  returned, assigned elsewhere or passed to a function that keeps it;
- a global set to a new handle that no function of the script destroys.

The types and functions are data, in
`src/wc3files/scripts/gamedata/leaks.json`: for each type (location, group,
force, effect, lightning, text tag, rect, region, timer, image, ubersplat,
boolexpr), the functions that create a new one on each call, those that
destroy it, and those it can be passed to and live on (hashtable saves,
`TimerStart`, region events). Functions of the script that return a new
handle on every path count as creating functions too.

These are not reported:

- the GUI idioms: `set bj_wantDestroyGroup = true` before the `ForGroupBJ`
  it applies to; a handle a BJ function also keeps in `bj_lastCreated...`,
  destroyed or stored through `GetLastCreatedEffectBJ()` and the like before
  the next one replaces it; a text tag made temporary with
  `SetTextTagPermanent(t, false)`;
- a handle passed to a function of the script, which may destroy it. Calling
  such a function or running a trigger also releases the `bj_lastCreated...`
  handles;
- the World Editor's `gg_` globals;
- code that runs once at map start: `main`, `config`, the functions only they
  call (also through `ExecuteFunc("name")`, as JassHelper runs library
  initializers), and the actions of a trigger whose one run is a map
  initialization or elapsed-time event. A handle lost there is lost once.

Known false positives and misses:

- a global kept for the whole game but set in a trigger that the check
  cannot tell runs once, such as a trigger turned off after its first run,
  is reported;
- an effect meant to stay, attached to a unit in code that runs more than
  once, is reported;
- a handle passed to a function of the script counts as handled even when
  that function does not destroy it;
- locals and globals are not followed in the order of the statements: a
  local set on every turn of a loop and destroyed once after it is not
  reported, and neither is a global overwritten before it is destroyed.

`--no-leaks` leaves the warnings out. Lua scripts are not checked for leaks.

On the 319 parsed `war3map.j` scripts of the benchmark corpus, the check
reports 70,806 warnings in 274 maps, 66,713 of them
dropped handles (mostly locations from `GetRectCenter` and `GetUnitLoc`,
groups from `GetUnitsInRect...` and forces from `GetForceOfPlayer`). Of 20
warnings drawn at random, one per map, and read in their scripts, 19 were
real leaks; the other was an effect attached to a unit to stay.

## Running a map script

The checker reads a script; `run-map` runs one, to find the errors a map
makes while it sets itself up. It is a Lua 5.3 interpreter with the game's
API installed:

1. every native of `common.j` as a Python stub, and `blizzard.j` translated
   to Lua, so the BJ functions are the real ones;
2. `config()` and `main()` called, as the game does;
3. a clock: timers, `TriggerRegisterTimerEvent` and `TriggerSleepAction`
   work, and `--seconds` of game time pass, so code started from a 0-second
   timer or an "elapsed game time" event runs too. Each callback runs in its
   own coroutine, as Reforged runs triggers.

```bash
wc3files run-map war3map.lua               # or a war3map.j, or a .w3x/.w3m map
wc3files run-map MyMap.w3x --seconds 30 --players 4 --messages
```

```python
from wc3files.scripts import MapRuntime

runtime = MapRuntime(players=4)
report = runtime.run(source, 'war3map.lua', seconds=30)
report.errors        # runtime errors, with the Lua position and a traceback
report.warnings      # natives called with an argument of the wrong type
report.messages      # what the map displayed (DisplayTextToPlayer, print)
runtime.advance(10)  # let more game time pass
```

A JASS map runs the same way: its `war3map.j` is translated to Lua first,
and the natives it declares itself (`UnitAlive`) get stubs.

What has state: players, units (owner, type, position, life), groups and
forces with their enumerations and filters, hashtables, triggers with their
conditions and actions, timers, locations, rects and camera bounds, and the
string, math and conversion natives, which compute their real results.
Every other native returns the default value of its type: 0, 0.0, false,
"", or a new handle of the declared type. Events other than timers never
fire, there is no object data (`BlzGetAbilityTooltip` returns ""), and the
`debug` library is absent, as it is in the game.

So an error is a lead, not a verdict: a map that reads its own object data,
or places things by the real terrain, can fail here and work in the game.
On the 36 real `war3map.lua` scripts of the development machine, 23 run 10
seconds of game time without an error (the Footmen Frenzy, Castle Fight,
Risk, Poker Strike, Spectrum TD and War of Races maps among them, up to
2,925 callbacks each). One error is the map's own: a call to a `globals`
that is defined nowhere. The other 12 are one Legion TD map reading numbers
out of an ability tooltip, which needs the object data this runtime does
not have. Running them also found two interpreter bugs, both fixed: weak
tables dropped an entry before any collection, and a 5.3 map's code took
the branch meant for a game with a `debug` library.

`jass --to-lua` prints the translation itself: JASS's truncating integer
division becomes `__jdiv`, string `+` becomes `..` (with null as ""),
arrays become `__jarray(default)`, and loops become `while true do`.

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

Measured against ground truth rather than estimated: on 302 unprotected maps
that ship *both* a script and the real `war3map.wtg`, the triggers were
rebuilt from the script alone and compared against what the editor had
stored.

| | recovered |
|---|---|
| triggers found | **98.6%** |
| their events, conditions and actions | **93.9%** |
| their parameters | **88.3%** |
| variables | **100%** |

Every GUI trigger was recovered in 291 of the 302 maps. (The parameter share
is of the functions that were matched, so it moves with them.)

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
  rawcode and the other an upgrade rawcode. The rawcode decides: the game's
  own unit and upgrade tables know the standard ones, and the map's custom
  units and upgrades come from its object data (`object_ids=`, see
  `object_ids_from_files`, or a map given to `script-to-triggers`). A rawcode
  that is neither keeps the name the script has.
- **vJass hooks** are seen through: JassHelper turns every call to a hooked
  native `X` into a call to its wrapper `h__X`, and a call to such a wrapper
  is read as a call to `X`.
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
wc3files script-to-triggers MyMap.w3x           # ... with the map's own objects
wc3files script-to-triggers war3map.j --json    # structured output
wc3files jass war3map.j                         # parse and print back
```
