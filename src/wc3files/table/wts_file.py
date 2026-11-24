import re

class WtsFile(object):
     # A "// Units: ..." comment line commonly sits between "STRING n" and its opening "{".
     # The text and its line break are optional, and tried last (`??`), so an
     # empty string written as "{" then "}" on the next line reads as empty
     # instead of running on into the next entry. The closing "}" must end its
     # line, so a text line that starts with "}" does not close the entry early.
     ENTRY_PATTERN = re.compile(
          r'STRING\s+(\d+)\s*(?://[^\n]*\r?\n\s*)*\{\r?\n(?:(.*?)\r?\n)??\}(?=\r?\n|$)',
          re.DOTALL)
     PLACEHOLDER_PATTERN = re.compile(r'^TRIGSTR_(\d+)$', re.IGNORECASE)

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, (bytes, bytearray)):
               raw = bytes(file_path)
          else:
               with open(file_path, 'rb') as f:
                    raw = f.read()

          try:
               text = raw.decode('utf-8-sig')
          except UnicodeDecodeError:
               text = raw.decode('cp1252', errors='replace')

          self._data = {int(string_id): value for string_id, value in self.ENTRY_PATTERN.findall(text)}

     def write(self, file_path):
          data = self.to_bytes()
          with open(file_path, 'wb') as f:
               f.write(data)

     def to_bytes(self):
          """The file's bytes, as write() saves them."""
          # Matches the format real WC3 tools emit: UTF-8 with a BOM, CRLF line endings,
          # and a blank line between entries.
          blocks = [f'STRING {string_id}\r\n{{\r\n{value}\r\n}}\r\n' for string_id, value in self._data.items()]
          return '\r\n'.join(blocks).encode('utf-8-sig')

     def import_data(self, data):
          self._data = data
          return self

     def export_data(self):
          return self._data

     # The name every File class has; export_data() stays (#174).
     get_data = export_data

     def get(self, string_id):
          if isinstance(string_id, str):
               match = self.PLACEHOLDER_PATTERN.match(string_id.strip())
               string_id = int(match.group(1)) if match else int(string_id)
          return self._data.get(string_id)

     def resolve(self, value):
          # Passes through anything that isn't a "TRIGSTR_XXXXX" placeholder, so it's safe to
          # call on every field value regardless of type.
          if not isinstance(value, str):
               return value
          match = self.PLACEHOLDER_PATTERN.match(value.strip())
          if not match:
               return value
          resolved = self._data.get(int(match.group(1)))
          return resolved if resolved is not None else value

     def __len__(self):
          return len(self._data)
