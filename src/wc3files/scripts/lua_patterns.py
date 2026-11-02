"""
Lua string patterns (`string.find`, `match`, `gmatch`, `gsub`).

A direct port of the matcher in Lua 5.1's `lstrlib.c`: Lua patterns are not
regular expressions (`%b()`, `%f[set]`, back-references `%1`, `-` as the lazy
`*`, no alternation), so translating them to Python's `re` would only be
approximately right. Strings are byte strings held in Python `str`, one
character per byte, as everywhere in `lua_vm`.

Positions in the public functions are 0-based Python indices; `lua_stdlib`
converts to and from Lua's 1-based positions.
"""

CAP_UNFINISHED = -1
CAP_POSITION = -2
MAX_CAPTURES = 32
MAX_MATCH_DEPTH = 200
SPECIALS = set('^$*+?.([%-')


class PatternError(Exception):
     """A malformed pattern. `lua_stdlib` turns it into a Lua error."""


def _match_class(c, cl):
     o = ord(c)
     lower = cl.lower()
     if lower == 'a':
          res = 'a' <= c <= 'z' or 'A' <= c <= 'Z'
     elif lower == 'c':
          res = o < 32 or o == 127
     elif lower == 'd':
          res = '0' <= c <= '9'
     elif lower == 'g':
          res = 33 <= o <= 126
     elif lower == 'l':
          res = 'a' <= c <= 'z'
     elif lower == 'p':
          res = (33 <= o <= 47 or 58 <= o <= 64 or 91 <= o <= 96
                 or 123 <= o <= 126)
     elif lower == 's':
          res = c in ' \t\n\r\f\v'
     elif lower == 'u':
          res = 'A' <= c <= 'Z'
     elif lower == 'w':
          res = 'a' <= c <= 'z' or 'A' <= c <= 'Z' or '0' <= c <= '9'
     elif lower == 'x':
          res = '0' <= c <= '9' or 'a' <= c <= 'f' or 'A' <= c <= 'F'
     elif lower == 'z':
          res = o == 0
     else:
          return cl == c
     if cl.isupper():
          return not res
     return res


class _MatchState:
     __slots__ = ('src', 'pat', 'slen', 'plen', 'capture', 'depth')

     def __init__(self, src, pat):
          self.src = src
          self.pat = pat
          self.slen = len(src)
          self.plen = len(pat)
          self.capture = []
          self.depth = MAX_MATCH_DEPTH

     def reset(self):
          self.capture = []
          self.depth = MAX_MATCH_DEPTH

     # -- pattern items ---------------------------------------------------

     def class_end(self, p):
          pat, plen = self.pat, self.plen
          c = pat[p]
          p += 1
          if c == '%':
               if p >= plen:
                    raise PatternError("malformed pattern (ends with '%')")
               return p + 1
          if c == '[':
               if p < plen and pat[p] == '^':
                    p += 1
               while True:
                    if p >= plen:
                         raise PatternError("malformed pattern (missing ']')")
                    cc = pat[p]
                    p += 1
                    if cc == '%' and p < plen:
                         p += 1
                    if p < plen and pat[p] == ']':
                         break
               return p + 1
          return p

     def match_bracket_class(self, c, p, ec):
          pat = self.pat
          sig = True
          if pat[p + 1] == '^':
               sig = False
               p += 1
          p += 1
          while p < ec:
               ch = pat[p]
               if ch == '%':
                    p += 1
                    if _match_class(c, pat[p]):
                         return sig
               elif p + 2 < ec and pat[p + 1] == '-':
                    p += 2
                    if pat[p - 2] <= c <= pat[p]:
                         return sig
               elif ch == c:
                    return sig
               p += 1
          return not sig

     def single_match(self, s, p, ep):
          if s >= self.slen:
               return False
          c = self.src[s]
          pc = self.pat[p]
          if pc == '.':
               return True
          if pc == '%':
               return _match_class(c, self.pat[p + 1])
          if pc == '[':
               return self.match_bracket_class(c, p, ep - 1)
          return pc == c

     # -- matcher ---------------------------------------------------------

     def do_match(self, s, p):
          self.depth -= 1
          if self.depth == 0:
               raise PatternError('pattern too complex')
          try:
               return self._do_match(s, p)
          finally:
               self.depth += 1

     def _do_match(self, s, p):
          pat, plen, src, slen = self.pat, self.plen, self.src, self.slen
          while True:
               if p == plen:
                    return s
               pc = pat[p]
               if pc == '(':
                    if p + 1 < plen and pat[p + 1] == ')':
                         return self.start_capture(s, p + 2, CAP_POSITION)
                    return self.start_capture(s, p + 1, CAP_UNFINISHED)
               if pc == ')':
                    return self.end_capture(s, p + 1)
               if pc == '$' and p + 1 == plen:
                    return s if s == slen else -1
               if pc == '%' and p + 1 < plen:
                    nc = pat[p + 1]
                    if nc == 'b':
                         s = self.match_balance(s, p + 2)
                         if s == -1:
                              return -1
                         p += 4
                         continue
                    if nc == 'f':
                         p += 2
                         if p >= plen or pat[p] != '[':
                              raise PatternError("missing '[' after '%f' in pattern")
                         ep = self.class_end(p)
                         prev = src[s - 1] if s > 0 else '\0'
                         cur = src[s] if s < slen else '\0'
                         if (not self.match_bracket_class(prev, p, ep - 1)
                                   and self.match_bracket_class(cur, p, ep - 1)):
                              p = ep
                              continue
                         return -1
                    if '0' <= nc <= '9':
                         s = self.match_capture(s, nc)
                         if s == -1:
                              return -1
                         p += 2
                         continue
               ep = self.class_end(p)
               epc = pat[ep] if ep < plen else ''
               if not self.single_match(s, p, ep):
                    if epc in ('*', '?', '-'):
                         p = ep + 1
                         continue
                    return -1
               if epc == '?':
                    res = self.do_match(s + 1, ep + 1)
                    if res != -1:
                         return res
                    p = ep + 1
                    continue
               if epc == '+':
                    return self.max_expand(s + 1, p, ep)
               if epc == '*':
                    return self.max_expand(s, p, ep)
               if epc == '-':
                    return self.min_expand(s, p, ep)
               s += 1
               p = ep

     def max_expand(self, s, p, ep):
          i = 0
          while self.single_match(s + i, p, ep):
               i += 1
          while i >= 0:
               res = self.do_match(s + i, ep + 1)
               if res != -1:
                    return res
               i -= 1
          return -1

     def min_expand(self, s, p, ep):
          while True:
               res = self.do_match(s, ep + 1)
               if res != -1:
                    return res
               if self.single_match(s, p, ep):
                    s += 1
               else:
                    return -1

     def start_capture(self, s, p, what):
          if len(self.capture) >= MAX_CAPTURES:
               raise PatternError('too many captures')
          self.capture.append([s, what])
          res = self.do_match(s, p)
          if res == -1:
               self.capture.pop()
          return res

     def end_capture(self, s, p):
          index = None
          for i in range(len(self.capture) - 1, -1, -1):
               if self.capture[i][1] == CAP_UNFINISHED:
                    index = i
                    break
          if index is None:
               raise PatternError('invalid pattern capture')
          self.capture[index][1] = s - self.capture[index][0]
          res = self.do_match(s, p)
          if res == -1:
               self.capture[index][1] = CAP_UNFINISHED
          return res

     def match_balance(self, s, p):
          if p + 1 >= self.plen:
               raise PatternError("malformed pattern (missing arguments to '%b')")
          src, slen = self.src, self.slen
          if s >= slen or src[s] != self.pat[p]:
               return -1
          b, e = self.pat[p], self.pat[p + 1]
          count = 1
          s += 1
          while s < slen:
               c = src[s]
               if c == e:
                    count -= 1
                    if count == 0:
                         return s + 1
               elif c == b:
                    count += 1
               s += 1
          return -1

     def match_capture(self, s, digit):
          index = ord(digit) - ord('1')
          if (index < 0 or index >= len(self.capture)
                    or self.capture[index][1] == CAP_UNFINISHED):
               raise PatternError(f'invalid capture index %{index + 1}')
          start, length = self.capture[index]
          if self.slen - s >= length and \
                    self.src[start:start + length] == self.src[s:s + length]:
               return s + length
          return -1

     # -- captures --------------------------------------------------------

     def one_capture(self, i, s, e):
          if i >= len(self.capture):
               if i == 0:
                    return self.src[s:e]
               raise PatternError(f'invalid capture index %{i + 1}')
          start, length = self.capture[i]
          if length == CAP_UNFINISHED:
               raise PatternError('unfinished capture')
          if length == CAP_POSITION:
               return start + 1
          return self.src[start:start + length]

     def captures(self, s, e, whole=True):
          """The captures of a match: positions are 1-based integers, as Lua
          returns them. With no explicit capture, the whole match when
          `whole`, nothing otherwise (string.find)."""
          n = len(self.capture)
          if n == 0 and whole:
               n = 1
          return [self.one_capture(i, s, e) for i in range(n)]


def _has_specials(pattern):
     return any(c in SPECIALS for c in pattern)


def find(src, pattern, init=0, plain=False, find_mode=True):
     """string.find (find_mode) or string.match from 0-based `init`.

     Returns None, or for find `[start, end, captures...]` (1-based,
     inclusive), for match the captures."""
     if init > len(src):
          return None
     if find_mode and (plain or not _has_specials(pattern)):
          index = src.find(pattern, init)
          if index < 0:
               return None
          return [index + 1, index + len(pattern)]
     ms = _MatchState(src, pattern)
     anchor = pattern.startswith('^')
     p = 1 if anchor else 0
     s1 = init
     while True:
          ms.reset()
          e = ms.do_match(s1, p)
          if e != -1:
               if find_mode:
                    return [s1 + 1, e] + ms.captures(None, None, whole=False)
               return ms.captures(s1, e)
          if anchor or s1 >= ms.slen:
               return None
          s1 += 1


def gmatch(src, pattern, lastmatch_rule=False):
     """Generator of capture lists, as string.gmatch iterates them.

     `lastmatch_rule` is Lua 5.3.4's: a match may not end where the previous
     one did, so an empty match right after a match is skipped. Lua 5.1
     instead steps one character past every empty match."""
     ms = _MatchState(src, pattern)
     s = 0
     last = -1
     while s <= ms.slen:
          ms.reset()
          e = ms.do_match(s, 0)
          if e != -1 and not (lastmatch_rule and e == last):
               result = ms.captures(s, e)
               if lastmatch_rule:
                    s = last = e
               else:
                    s = e + 1 if e == s else e
               yield result
          else:
               s += 1


def gsub(src, pattern, repl, max_n=None, lastmatch_rule=False):
     """string.gsub. `repl` is a string with `%0`-`%9`, or a callable taking
     the capture list and returning a replacement string, or None to keep the
     match. Returns (text, count)."""
     ms = _MatchState(src, pattern)
     anchor = pattern.startswith('^')
     p = 1 if anchor else 0
     s = 0
     n = 0
     last = -1
     out = []
     while max_n is None or n < max_n:
          ms.reset()
          e = ms.do_match(s, p)
          if lastmatch_rule:
               # Lua 5.3.4: no match may end where the previous one did
               if e != -1 and e != last:
                    n += 1
                    out.append(_replacement(ms, s, e, repl, True))
                    s = last = e
               elif s < ms.slen:
                    out.append(src[s])
                    s += 1
               else:
                    break
               if anchor:
                    break
               continue
          if e != -1:
               n += 1
               out.append(_replacement(ms, s, e, repl))
          if e != -1 and e > s:
               s = e
          elif s < ms.slen:
               out.append(src[s])
               s += 1
          else:
               break
          if anchor:
               break
     out.append(src[s:])
     return ''.join(out), n


def _replacement(ms, s, e, repl, strict=False):
     whole = ms.src[s:e]
     if isinstance(repl, str):
          if '%' not in repl:
               return repl
          out = []
          i = 0
          n = len(repl)
          while i < n:
               c = repl[i]
               if c == '%' and i + 1 < n:
                    i += 1
                    d = repl[i]
                    if d == '0':
                         out.append(whole)
                    elif '1' <= d <= '9':
                         value = ms.one_capture(ord(d) - ord('1'), s, e)
                         out.append(str(value))
                    elif strict and d != '%':
                         # 5.2+: only %0-%9 and %% are allowed
                         raise PatternError("invalid use of '%' in replacement string")
                    else:
                         out.append(d)
               else:
                    out.append(c)
               i += 1
          return ''.join(out)
     value = repl(ms.captures(s, e))
     return whole if value is None else value
