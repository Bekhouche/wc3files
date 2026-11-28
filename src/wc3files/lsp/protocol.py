"""
The transport of the Language Server Protocol: JSON-RPC 2.0 messages, each
framed by a `Content-Length` header, over a pair of binary streams (the
server's stdin and stdout). Written on the standard library, so the server
needs no extra package.
"""

import json


class ProtocolError(ValueError):
    """A message that is not framed or encoded as the protocol says."""


def read_message(stream):
    """The next message from `stream` as a dict, or None at the end of the
    stream."""
    length = None
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break  # the blank line that ends the headers
        name, _, value = line.decode('ascii', 'replace').partition(':')
        if name.strip().lower() == 'content-length':
            try:
                length = int(value.strip())
            except ValueError:
                raise ProtocolError(f'bad Content-Length: {value.strip()!r}') from None
    if length is None:
        raise ProtocolError('a message without a Content-Length header')
    body = stream.read(length)
    if len(body) < length:
        return None
    try:
        return json.loads(body.decode('utf-8'))
    except ValueError as error:
        raise ProtocolError(f'a message that is not JSON: {error}') from None


def write_message(stream, message):
    """Write `message` (a dict) to `stream`, framed, and flush it."""
    body = json.dumps(message, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    stream.write(f'Content-Length: {len(body)}\r\n\r\n'.encode('ascii') + body)
    stream.flush()
