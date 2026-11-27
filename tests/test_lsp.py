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
