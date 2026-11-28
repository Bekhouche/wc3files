"""
The language server: one document store and a handler per method.

Documents are synced in full (the editor sends the whole text on every
change), and checked again on each change. That keeps the server simple
and is fast enough for map scripts: the largest war3map.j of the first 120
corpus maps, 89,392 lines, is checked in 1.9 s.
"""

import sys
import traceback

from . import analysis
from .protocol import ProtocolError, read_message, write_message

# JSON-RPC and protocol error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002

FULL_SYNC = 1


class Document:
    def __init__(self, uri, text, language, version=None):
        self.uri = uri
        self.language = language
        self.version = version
        self.text = text

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value
        self._index = None  # built again on the next request that needs it

    @property
    def index(self):
        if self._index is None:
            from .symbols import index
            self._index = index(self.language, self._text)
        return self._index


class Server:
    """Reads messages from `reader` and writes replies and notifications to
    `writer`, both binary streams; `run()` serves until `exit`."""

    def __init__(self, reader, writer, log=None):
        self.reader = reader
        self.writer = writer
        self.log = log or sys.stderr
        self.documents = {}
        self.initialized = False
        self.shutting_down = False
        self.exit_code = None

    # -- transport ---------------------------------------------------------

    def send(self, message):
        message = dict(message, jsonrpc='2.0')
        write_message(self.writer, message)

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def reply(self, request_id, result=None, error=None):
        if error is not None:
            self.send({'id': request_id, 'error': error})
        else:
            self.send({'id': request_id, 'result': result})

    def run(self):
        """Serve until the client sends `exit` or closes the stream. Returns
        the exit code: 0 after a shutdown request, 1 otherwise, as the
        protocol asks."""
        while self.exit_code is None:
            try:
                message = read_message(self.reader)
            except ProtocolError as error:
                self.reply(None, error={'code': PARSE_ERROR, 'message': str(error)})
                continue
            if message is None:
                return 0 if self.shutting_down else 1
            self.handle(message)
        return self.exit_code

    def handle(self, message):
        method = message.get('method')
        request_id = message.get('id')
        is_request = 'id' in message
        if not isinstance(method, str):
            if is_request:
                self.reply(request_id, error={'code': INVALID_REQUEST, 'message': 'no method'})
            return
        if not self.initialized and method not in ('initialize', 'exit'):
            if is_request:
                self.reply(request_id, error={'code': SERVER_NOT_INITIALIZED,
                                              'message': 'the server is not initialized'})
            return
        handler = self.handlers().get(method)
        if handler is None:
            if is_request:
                self.reply(request_id, error={'code': METHOD_NOT_FOUND,
                                              'message': f'unsupported method: {method}'})
            return  # an unknown notification is ignored, as the protocol says
        try:
            result = handler(message.get('params') or {})
        except Exception as error:  # one bad document must not stop the server
            traceback.print_exc(file=self.log)
            if is_request:
                self.reply(request_id, error={'code': INTERNAL_ERROR, 'message': str(error)})
            return
        if is_request:
            self.reply(request_id, result)

    def handlers(self):
        return {
            'initialize': self.on_initialize,
            'initialized': lambda params: None,
            'shutdown': self.on_shutdown,
            'exit': self.on_exit,
            'textDocument/didOpen': self.on_did_open,
            'textDocument/didChange': self.on_did_change,
            'textDocument/didSave': self.on_did_save,
            'textDocument/didClose': self.on_did_close,
            'textDocument/hover': self.on_hover,
        }

    # -- lifecycle ---------------------------------------------------------

    def capabilities(self):
        return {
            'textDocumentSync': {'openClose': True, 'change': FULL_SYNC,
                                 'save': {'includeText': True}},
            'hoverProvider': True,
        }

    def on_initialize(self, params):
        from wc3files.version import __version__
        self.initialized = True
        return {'capabilities': self.capabilities(),
                'serverInfo': {'name': 'wc3files-lsp', 'version': __version__}}

    def on_shutdown(self, params):
        self.shutting_down = True
        return None

    def on_exit(self, params):
        self.exit_code = 0 if self.shutting_down else 1

    # -- documents ---------------------------------------------------------

    def on_did_open(self, params):
        item = params['textDocument']
        uri = item['uri']
        language = analysis.language_of(uri, item.get('languageId'))
        self.documents[uri] = Document(uri, item.get('text', ''), language, item.get('version'))
        self.publish(uri)

    def on_did_change(self, params):
        uri = params['textDocument']['uri']
        document = self.documents.get(uri)
        if document is None:
            return
        changes = params.get('contentChanges') or []
        if changes:
            # Full sync: the last change holds the whole text.
            document.text = changes[-1].get('text', document.text)
        document.version = params['textDocument'].get('version')
        self.publish(uri)

    def on_did_save(self, params):
        uri = params['textDocument']['uri']
        document = self.documents.get(uri)
        if document is None:
            return
        if 'text' in params:
            document.text = params['text']
        self.publish(uri)

    def on_did_close(self, params):
        uri = params['textDocument']['uri']
        self.documents.pop(uri, None)
        # Clear what the editor shows for a closed file.
        self.notify('textDocument/publishDiagnostics', {'uri': uri, 'diagnostics': []})

    # -- language features -------------------------------------------------

    def symbol_at(self, params):
        """(document, symbol, word range) for the name at the request's
        position, or None."""
        from .symbols import lookup, word_at
        document = self.documents.get(params['textDocument']['uri'])
        if document is None or document.language is None:
            return None
        position = params['position']
        found = word_at(document.text, position['line'], position['character'])
        if found is None:
            return None
        word, start, end = found
        symbol = lookup(document.index, word, position['line'] + 1)
        if symbol is None:
            return None
        line = position['line']
        return document, symbol, {'start': {'line': line, 'character': start},
                                  'end': {'line': line, 'character': end}}

    def on_hover(self, params):
        from .symbols import describe
        found = self.symbol_at(params)
        if found is None:
            return None
        document, symbol, word_range = found
        return {'contents': {'kind': 'markdown', 'value': describe(symbol, document.language)},
                'range': word_range}

    def publish(self, uri):
        document = self.documents[uri]
        params = {'uri': uri, 'diagnostics': analysis.diagnostics(document.language, document.text)}
        if document.version is not None:
            params['version'] = document.version
        self.notify('textDocument/publishDiagnostics', params)


def serve(reader=None, writer=None):
    """Serve on `reader` and `writer`, by default stdin and stdout."""
    reader = reader or sys.stdin.buffer
    writer = writer or sys.stdout.buffer
    return Server(reader, writer).run()
