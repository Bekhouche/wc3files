"""Real tests for wc3files.lsp: the server is driven through in-memory pipes
with the messages an editor sends, and once as a process over stdio."""
import io
import os
import subprocess
import sys
import unittest

from wc3files.lsp import analysis
from wc3files.lsp.protocol import ProtocolError, read_message, write_message
from wc3files.lsp.server import Server

GOOD_JASS = (
    'function Hello takes nothing returns nothing\n'
    '    call BJDebugMsg("hello")\n'
    'endfunction\n')

BAD_CALL = (
    'function Hello takes nothing returns nothing\n'
    '    call BJDebugMsg("hello", 2)\n'
    'endfunction\n')

LEAK = (
    'function Hello takes nothing returns nothing\n'
    '    local location p = GetRectCenter(GetPlayableMapRect())\n'
    'endfunction\n')

SYNTAX_ERROR = (
    'function Hello takes nothing returns nothing\n'
    '    call BJDebugMsg("hello"\n'
    'endfunction\n')


def frame(message):
    stream = io.BytesIO()
    write_message(stream, dict(message, jsonrpc='2.0'))
    return stream.getvalue()


def exchange(*messages):
    """Run a server on `messages` and give back what it wrote."""
    reader = io.BytesIO(b''.join(frame(m) for m in messages))
    writer = io.BytesIO()
    code = Server(reader, writer, log=io.StringIO()).run()
    writer.seek(0)
    out = []
    while True:
        message = read_message(writer)
        if message is None:
            return code, out
        out.append(message)


INITIALIZE = {'id': 1, 'method': 'initialize', 'params': {'capabilities': {}}}
SHUTDOWN = {'id': 99, 'method': 'shutdown'}
EXIT = {'method': 'exit'}


def open_doc(text, uri='file:///map/war3map.j', language='jass', version=1):
    return {'method': 'textDocument/didOpen', 'params': {'textDocument': {
        'uri': uri, 'languageId': language, 'version': version, 'text': text}}}


def published(out):
    return [m['params'] for m in out if m.get('method') == 'textDocument/publishDiagnostics']


class TestProtocol(unittest.TestCase):
    def test_framing_round_trip(self):
        message = {'jsonrpc': '2.0', 'id': 3, 'method': 'x', 'params': {'text': 'é'}}
        stream = io.BytesIO()
        write_message(stream, message)
        self.assertTrue(stream.getvalue().startswith(b'Content-Length: '))
        stream.seek(0)
        self.assertEqual(read_message(stream), message)
        self.assertIsNone(read_message(stream))

    def test_a_message_without_a_length(self):
        with self.assertRaises(ProtocolError):
            read_message(io.BytesIO(b'Content-Type: x\r\n\r\n{}'))


class TestLifecycle(unittest.TestCase):
    def test_initialize_shutdown_exit(self):
        code, out = exchange(INITIALIZE, {'method': 'initialized', 'params': {}}, SHUTDOWN, EXIT)
        self.assertEqual(code, 0)
        result = out[0]['result']
        self.assertEqual(result['serverInfo']['name'], 'wc3files-lsp')
        self.assertEqual(result['capabilities']['textDocumentSync']['change'], 1)
        self.assertEqual(out[1], {'jsonrpc': '2.0', 'id': 99, 'result': None})

    def test_exit_without_shutdown_is_code_1(self):
        code, _ = exchange(INITIALIZE, EXIT)
        self.assertEqual(code, 1)

    def test_a_request_before_initialize(self):
        _, out = exchange({'id': 1, 'method': 'textDocument/hover', 'params': {}}, EXIT)
        self.assertEqual(out[0]['error']['code'], -32002)

    def test_an_unknown_request_and_notification(self):
        _, out = exchange(INITIALIZE, {'id': 2, 'method': 'wc3/unknown'},
                          {'method': '$/unknownNotification'}, SHUTDOWN, EXIT)
        self.assertEqual(out[1]['error']['code'], -32601)
        self.assertEqual(out[2]['id'], 99)  # the notification got no reply


class TestDiagnostics(unittest.TestCase):
    def diagnostics(self, text, **kwargs):
        _, out = exchange(INITIALIZE, open_doc(text, **kwargs), SHUTDOWN, EXIT)
        return published(out)[0]

    def test_a_clean_script(self):
        params = self.diagnostics(GOOD_JASS)
        self.assertEqual((params['uri'], params['version'], params['diagnostics']),
                         ('file:///map/war3map.j', 1, []))

    def test_a_wrong_argument_count(self):
        [diagnostic] = self.diagnostics(BAD_CALL)['diagnostics']
        self.assertEqual(diagnostic['severity'], analysis.ERROR)
        self.assertEqual(diagnostic['range']['start'], {'line': 1, 'character': 4})
        self.assertIn('BJDebugMsg', diagnostic['message'])

    def test_a_leak_is_a_warning(self):
        diagnostics = self.diagnostics(LEAK)['diagnostics']
        self.assertTrue(diagnostics)
        self.assertTrue(all(d['severity'] == analysis.WARNING for d in diagnostics))
        self.assertEqual(diagnostics[0]['range']['start']['line'], 1)

    def test_a_syntax_error_has_its_column(self):
        [diagnostic] = self.diagnostics(SYNTAX_ERROR)['diagnostics']
        self.assertEqual(diagnostic['severity'], analysis.ERROR)
        self.assertEqual(diagnostic['range']['start']['line'], 2)
        self.assertNotIn(' at line ', diagnostic['message'])

    def test_lua(self):
        params = self.diagnostics('print(UndefinedThing)\nBJDebugMsg("a", 2)\n',
                                  uri='file:///map/war3map.lua', language='lua')
        lines = {d['range']['start']['line'] for d in params['diagnostics']}
        self.assertIn(1, lines)

    def test_the_language_from_the_extension(self):
        params = self.diagnostics(BAD_CALL, uri='file:///map/Script.j', language='plaintext')
        self.assertEqual(len(params['diagnostics']), 1)
        params = self.diagnostics(BAD_CALL, uri='file:///notes.txt', language='plaintext')
        self.assertEqual(params['diagnostics'], [])

    def test_change_save_and_close(self):
        uri = 'file:///map/war3map.j'
        _, out = exchange(
            INITIALIZE, open_doc(GOOD_JASS),
            {'method': 'textDocument/didChange', 'params': {
                'textDocument': {'uri': uri, 'version': 2}, 'contentChanges': [{'text': BAD_CALL}]}},
            {'method': 'textDocument/didSave', 'params': {'textDocument': {'uri': uri}, 'text': GOOD_JASS}},
            {'method': 'textDocument/didClose', 'params': {'textDocument': {'uri': uri}}},
            SHUTDOWN, EXIT)
        counts = [len(p['diagnostics']) for p in published(out)]
        self.assertEqual(counts, [0, 1, 0, 0])
        self.assertEqual(published(out)[1]['version'], 2)


SCRIPT = (
    'globals\n'
    '    constant integer MAX_HEROES = 12\n'
    '    unit array udg_Heroes\n'
    'endglobals\n'
    'function Heal takes unit target, real amount returns nothing\n'
    '    local real life = GetUnitState(target, UNIT_STATE_LIFE)\n'
    '    call SetUnitState(target, UNIT_STATE_LIFE, life + amount)\n'
    'endfunction\n'
    'function Init takes nothing returns nothing\n'
    '    call Heal(udg_Heroes[MAX_HEROES], 10.0)\n'
    '    call BJDebugMsg("ready")\n'
    'endfunction\n')

LUA_SCRIPT = (
    'local count = 0\n'
    'function Spawn(owner)\n'
    '    count = count + 1\n'
    '    return CreateUnit(owner, FourCC("hfoo"), 0, 0, 0)\n'
    'end\n'
    'Spawn(Player(0))\n')


def hover(text, line, character, uri='file:///map/war3map.j', language='jass'):
    _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language),
                      {'id': 5, 'method': 'textDocument/hover', 'params': {
                          'textDocument': {'uri': uri},
                          'position': {'line': line, 'character': character}}},
                      SHUTDOWN, EXIT)
    [reply] = [m for m in out if m.get('id') == 5]
    return reply['result']


class TestHover(unittest.TestCase):
    def value(self, *args, **kwargs):
        result = hover(*args, **kwargs)
        return result and result['contents']['value']

    def test_a_native(self):
        result = hover(SCRIPT, 5, 24)  # GetUnitState
        self.assertEqual(result['range'], {'start': {'line': 5, 'character': 22},
                                           'end': {'line': 5, 'character': 34}})
        self.assertIn('native GetUnitState takes unit whichUnit, unitstate whichUnitState '
                      'returns real', result['contents']['value'])
        self.assertIn('common.j', result['contents']['value'])

    def test_a_bj_function_and_a_constant(self):
        self.assertIn('function BJDebugMsg takes string msg returns nothing', self.value(SCRIPT, 10, 10))
        self.assertIn('constant unitstate UNIT_STATE_LIFE', self.value(SCRIPT, 6, 30))

    def test_the_files_function_and_globals(self):
        self.assertIn('function Heal takes unit target, real amount returns nothing\n```\n'
                      'function, this file, line 5', self.value(SCRIPT, 9, 10))
        self.assertIn('constant integer MAX_HEROES = 12', self.value(SCRIPT, 9, 28))
        self.assertIn('unit array udg_Heroes', self.value(SCRIPT, 9, 16))

    def test_a_parameter_and_a_local(self):
        value = self.value(SCRIPT, 6, 56)  # amount
        self.assertIn('real amount', value)
        self.assertIn('parameter of `Heal`', value)
        self.assertIn('local of `Heal`', self.value(SCRIPT, 6, 48))  # life

    def test_nothing_under_the_cursor(self):
        self.assertIsNone(hover(SCRIPT, 3, 0))           # endglobals: a keyword
        self.assertIsNone(hover(SCRIPT, 10, 25))         # inside a string
        self.assertIsNone(hover(SCRIPT, 40, 0))          # past the end

    def test_a_file_that_does_not_parse(self):
        broken = SCRIPT.replace('endfunction\nfunction Init', 'function Init', 1)
        self.assertIn('function Heal', self.value(broken, 8, 10))

    def test_lua(self):
        uri, language = 'file:///map/war3map.lua', 'lua'
        self.assertIn('native CreateUnit takes player id', self.value(LUA_SCRIPT, 3, 12, uri=uri, language=language))
        self.assertIn('function Spawn(owner)', self.value(LUA_SCRIPT, 5, 1, uri=uri, language=language))
        self.assertIn('local count = 0', self.value(LUA_SCRIPT, 2, 5, uri=uri, language=language))


def definition(text, line, character, uri='file:///map/war3map.j', language='jass'):
    _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language),
                      {'id': 6, 'method': 'textDocument/definition', 'params': {
                          'textDocument': {'uri': uri},
                          'position': {'line': line, 'character': character}}},
                      SHUTDOWN, EXIT)
    [reply] = [m for m in out if m.get('id') == 6]
    return reply['result']


class TestDefinition(unittest.TestCase):
    def start(self, result):
        return result['range']['start']['line'], result['range']['start']['character']

    def test_the_files_function_global_and_constant(self):
        result = definition(SCRIPT, 9, 10)  # Heal
        self.assertEqual((result['uri'], self.start(result)), ('file:///map/war3map.j', (4, 9)))
        self.assertEqual(result['range']['end']['character'], 13)
        self.assertEqual(self.start(definition(SCRIPT, 9, 16)), (2, 15))   # udg_Heroes
        self.assertEqual(self.start(definition(SCRIPT, 9, 30)), (1, 21))   # MAX_HEROES

    def test_a_parameter_and_a_local(self):
        self.assertEqual(self.start(definition(SCRIPT, 6, 56)), (4, 38))  # amount
        self.assertEqual(self.start(definition(SCRIPT, 6, 48)), (5, 15))  # life

    def test_a_native_opens_the_bundled_common_j(self):
        import urllib.parse
        import urllib.request
        result = definition(SCRIPT, 5, 24)  # GetUnitState
        self.assertTrue(result['uri'].startswith('file:'))
        path = urllib.request.url2pathname(urllib.parse.urlparse(result['uri']).path)
        self.assertEqual(os.path.basename(path), 'common.j')
        with open(path, encoding='utf-8', errors='replace') as handle:
            line = handle.read().splitlines()[result['range']['start']['line']]
        character = result['range']['start']['character']
        self.assertEqual(line[character:character + len('GetUnitState')], 'GetUnitState')

    def test_lua_and_nothing(self):
        result = definition(LUA_SCRIPT, 5, 1, uri='file:///map/war3map.lua', language='lua')
        self.assertEqual(self.start(result), (1, 9))
        self.assertIsNone(definition(SCRIPT, 3, 0))


def complete(text, line, character, uri='file:///map/war3map.j', language='jass'):
    _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language),
                      {'id': 7, 'method': 'textDocument/completion', 'params': {
                          'textDocument': {'uri': uri},
                          'position': {'line': line, 'character': character}}},
                      SHUTDOWN, EXIT)
    [reply] = [m for m in out if m.get('id') == 7]
    return reply['result']


class TestCompletion(unittest.TestCase):
    def labels(self, result):
        return [item['label'] for item in result['items']]

    def with_line(self, line_text):
        """SCRIPT with `line_text` added in Heal, and its position."""
        lines = SCRIPT.splitlines()
        lines.insert(7, line_text)
        return '\n'.join(lines) + '\n', 7, len(line_text)

    def test_natives_bj_functions_and_the_files_names(self):
        text, line, character = self.with_line('    call GetUnitSt')
        result = complete(text, line, character)
        labels = self.labels(result)
        self.assertIn('GetUnitState', labels)
        item = result['items'][labels.index('GetUnitState')]
        self.assertEqual(item['kind'], 3)
        self.assertIn('takes unit whichUnit', item['detail'])
        text, line, character = self.with_line('    call He')
        self.assertIn('Heal', self.labels(complete(text, line, character)))
        text, line, character = self.with_line('    set udg_H')
        self.assertEqual(self.labels(complete(text, line, character))[0], 'udg_Heroes')

    def test_locals_only_in_their_function(self):
        text, line, character = self.with_line('    set li')
        self.assertIn('life', self.labels(complete(text, line, character)))
        lines = SCRIPT.splitlines()
        lines.insert(10, '    set li')  # in Init, where life is not declared
        self.assertNotIn('life', self.labels(complete('\n'.join(lines) + '\n', 10, 10)))

    def test_no_prefix_offers_the_scope_and_keywords(self):
        text, line, character = self.with_line('    ')
        result = complete(text, line, character)
        labels = self.labels(result)
        self.assertTrue({'target', 'amount', 'life', 'call', 'set'} <= set(labels))
        self.assertNotIn('GetUnitState', labels)
        self.assertTrue(result['isIncomplete'])

    def test_a_long_list_is_cut(self):
        text, line, character = self.with_line('    call S')
        result = complete(text, line, character)
        self.assertEqual(len([i for i in result['items'] if i['kind'] != 14]), 300)
        self.assertTrue(result['isIncomplete'])

    def test_lua(self):
        text = LUA_SCRIPT + 'Spa'
        result = complete(text, 6, 3, uri='file:///map/war3map.lua', language='lua')
        self.assertIn('Spawn', self.labels(result))
        result = complete(LUA_SCRIPT + 'CreateUn', 6, 8, uri='file:///map/war3map.lua', language='lua')
        self.assertIn('CreateUnit', self.labels(result))
        result = complete(LUA_SCRIPT + 'whi', 6, 3, uri='file:///map/war3map.lua', language='lua')
        self.assertIn('while', self.labels(result))


def outline(text, uri='file:///map/war3map.j', language='jass'):
    _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language),
                      {'id': 8, 'method': 'textDocument/documentSymbol', 'params': {
                          'textDocument': {'uri': uri}}},
                      SHUTDOWN, EXIT)
    [reply] = [m for m in out if m.get('id') == 8]
    return reply['result']


class TestDocumentSymbols(unittest.TestCase):
    def test_jass(self):
        symbols = outline(SCRIPT)
        self.assertEqual([(s['name'], s['kind']) for s in symbols],
                         [('MAX_HEROES', 14), ('udg_Heroes', 13), ('Heal', 12), ('Init', 12)])
        heal = symbols[2]
        self.assertEqual((heal['range']['start']['line'], heal['range']['end']['line']), (4, 7))
        self.assertEqual(heal['selectionRange']['start'], {'line': 4, 'character': 9})
        self.assertEqual([c['name'] for c in heal['children']], ['target', 'amount', 'life'])
        self.assertEqual(symbols[3]['children'], [])

    def test_a_function_without_its_endfunction(self):
        # While typing: Heal has no endfunction yet, and ends where Init starts;
        # its parameters stay its own.
        broken = SCRIPT.replace('endfunction\nfunction Init', 'function Init', 1)
        symbols = outline(broken)
        heal = [s for s in symbols if s['name'] == 'Heal'][0]
        self.assertEqual(heal['range']['end']['line'], 6)
        self.assertEqual([c['name'] for c in heal['children']], ['target', 'amount', 'life'])
        self.assertNotIn('target', [s['name'] for s in symbols])

    def test_lua(self):
        symbols = outline(LUA_SCRIPT, uri='file:///map/war3map.lua', language='lua')
        self.assertEqual([s['name'] for s in symbols], ['Spawn'])


VJASS = (
    'library Greeter initializer Init\n'
    '    function Greet takes nothing returns nothing\n'
    '        call BJDebugMsg("hello")\n'
    '    endfunction\n'
    '    private function Init takes nothing returns nothing\n'
    '        call Greet()\n'
    '    endfunction\n'
    'endlibrary\n')


class TestVJassDiagnostics(unittest.TestCase):
    def diagnostics(self, text, uri='file:///map/Greeter.j', language='jass'):
        _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language), SHUTDOWN, EXIT)
        return [(d['range']['start']['line'], d['severity'], d['message'])
                for d in published(out)[0]['diagnostics']]

    def test_a_clean_library(self):
        self.assertEqual(self.diagnostics(VJASS), [])

    def test_a_finding_at_its_source_line(self):
        text = VJASS.replace('BJDebugMsg("hello")', 'BJDebugMsg("hello", 3)')
        self.assertEqual(self.diagnostics(text),
                         [(2, analysis.ERROR, 'BJDebugMsg takes 1 argument(s), 2 given')])

    def test_a_leak_at_its_source_line(self):
        # In a function the initializer does not call: code that runs once is
        # not checked for leaks.
        text = VJASS.replace('endlibrary\n',
                             '    function Leaky takes nothing returns nothing\n'
                             '        local location p = GetRectCenter(GetPlayableMapRect())\n'
                             '    endfunction\n'
                             'endlibrary\n')
        found = self.diagnostics(text)
        self.assertTrue(found)
        self.assertEqual({(line, severity) for line, severity, _ in found}, {(8, analysis.WARNING)})

    def test_a_compile_error(self):
        [(line, severity, message)] = self.diagnostics(VJASS.replace('endlibrary\n', ''))
        self.assertEqual((severity, message), (analysis.ERROR, 'Missing endlibrary'))

    def test_vjass_by_extension_and_plain_jass_stays_jass(self):
        text = VJASS.replace('BJDebugMsg("hello")', 'BJDebugMsg("hello", 3)')
        self.assertEqual(len(self.diagnostics(text, uri='file:///map/Greeter.vj', language='plaintext')), 1)
        self.assertEqual(analysis.effective_language(analysis.JASS, BAD_CALL), analysis.JASS)
        self.assertEqual(analysis.effective_language(analysis.JASS, VJASS), analysis.VJASS)

    def test_zinc(self):
        text = ('library Z {\n'
                '    function onInit() {\n'
                '        BJDebugMsg("a", 2);\n'
                '    }\n'
                '}\n')
        self.assertEqual(self.diagnostics(text, uri='file:///map/z.zn', language='zinc'),
                         [(2, analysis.ERROR, 'BJDebugMsg takes 1 argument(s), 2 given')])

    def test_an_error_in_an_imported_file(self):
        import pathlib
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'Other.j'), 'w', encoding='utf-8') as handle:
                handle.write('library Other\nendlibrar\n')
            uri = pathlib.Path(folder, 'Main.j').as_uri()
            [(line, severity, message)] = self.diagnostics('//! import "Other.j"\n' + VJASS, uri=uri)
        self.assertEqual((line, severity), (0, analysis.ERROR))
        self.assertIn('Other.j', message)


def request(method, text, line, character, uri='file:///map/war3map.j', language='jass',
            **extra):
    """The result of `method` at a position in a document `text`."""
    params = dict({'textDocument': {'uri': uri},
                   'position': {'line': line, 'character': character}}, **extra)
    _, out = exchange(INITIALIZE, open_doc(text, uri=uri, language=language),
                      {'id': 5, 'method': method, 'params': params}, SHUTDOWN, EXIT)
    [reply] = [m for m in out if m.get('id') == 5]
    return reply['result']


SHADOWED = (
    'globals\n'
    '    integer count = 0\n'
    'endglobals\n'
    'function A takes nothing returns nothing\n'
    '    local integer count = 1\n'
    '    set count = count + 1 // count\n'
    'endfunction\n'
    'function B takes nothing returns nothing\n'
    '    set count = count + 2\n'
    '    call BJDebugMsg("count")\n'
    '    call A()\n'
    'endfunction\n')


def starts(locations):
    return [(r['range']['start']['line'], r['range']['start']['character']) for r in locations]


class TestReferences(unittest.TestCase):
    def references(self, text, line, character, include=True, **kwargs):
        return request('textDocument/references', text, line, character,
                       context={'includeDeclaration': include}, **kwargs)

    def test_a_local_is_not_the_global_of_the_same_name(self):
        result = self.references(SHADOWED, 5, 9)  # count in A
        self.assertEqual(starts(result), [(4, 18), (5, 8), (5, 16)])
        self.assertEqual({r['uri'] for r in result}, {'file:///map/war3map.j'})

    def test_a_global_outside_the_function_that_shadows_it(self):
        # Not in A, where `count` is the local; not in the string or comment.
        self.assertEqual(starts(self.references(SHADOWED, 8, 9)), [(1, 12), (8, 8), (8, 16)])

    def test_without_the_declaration(self):
        self.assertEqual(starts(self.references(SHADOWED, 1, 12, include=False)),
                         [(8, 8), (8, 16)])

    def test_a_function_and_a_native(self):
        self.assertEqual(starts(self.references(SCRIPT, 9, 10)), [(4, 9), (9, 9)])  # Heal
        self.assertEqual(starts(self.references(SCRIPT, 6, 10)), [(6, 9)])  # SetUnitState

    def test_a_parameter(self):
        self.assertEqual(starts(self.references(SCRIPT, 6, 22)),
                         [(4, 25), (5, 35), (6, 22)])  # target

    def test_lua(self):
        result = self.references(LUA_SCRIPT, 2, 4, uri='file:///map/war3map.lua', language='lua')
        self.assertEqual(starts(result), [(0, 6), (2, 4), (2, 12)])  # count

    def test_a_field_is_not_a_use(self):
        text = 'local x = 1\nlocal t = {}\nt.x = x\n'
        result = self.references(text, 0, 6, uri='file:///map/war3map.lua', language='lua')
        self.assertEqual(starts(result), [(0, 6), (2, 6)])

    def test_nothing_under_the_cursor(self):
        self.assertIsNone(self.references(SHADOWED, 3, 0))  # `function`


class TestDocumentHighlight(unittest.TestCase):
    def test_the_declaration_is_a_write(self):
        result = request('textDocument/documentHighlight', SHADOWED, 4, 18)
        self.assertEqual([(r['range']['start']['line'], r['kind']) for r in result],
                         [(4, 3), (5, 2), (5, 2)])


class TestProcess(unittest.TestCase):
    def test_over_stdio(self):
        src = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src')
        env = dict(os.environ, PYTHONPATH=src + os.pathsep + os.environ.get('PYTHONPATH', ''))
        data = b''.join(frame(m) for m in (INITIALIZE, open_doc(BAD_CALL), SHUTDOWN, EXIT))
        result = subprocess.run([sys.executable, '-m', 'wc3files.lsp', '--stdio'], input=data,
                                capture_output=True, env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', 'replace'))
        stream = io.BytesIO(result.stdout)
        messages = []
        while True:
            message = read_message(stream)
            if message is None:
                break
            messages.append(message)
        self.assertEqual(len(published(messages)[0]['diagnostics']), 1)
        self.assertNotIn(b'Content-Length', result.stderr)


if __name__ == '__main__':
    unittest.main()
