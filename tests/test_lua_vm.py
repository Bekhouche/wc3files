"""Tests for wc3files.scripts.lua_vm, the Lua interpreter, and its standard
library (lua_stdlib) and pattern matcher (lua_patterns).

The interpreter was also checked against the official Lua 5.1 test suite
(lua.org/tests, lua5.1-tests): closure, constructs, events, literals, locals,
math, nextvar, pm, sort, strings, vararg, attrib, code and checktable pass
unchanged. The parts that fail depend on Lua's one-pass compiler (load()
reading a chunk lazily, a limit error found before a later syntax error) or
on POSIX file semantics. That suite is not checked in; the cases below
cover the same ground in small pieces.
"""
import os
import shutil
import tempfile
import unittest

import _helpers  # noqa: F401
from wc3files.scripts.lua_vm import LuaVM, LuaError, run_with_stack
from wc3files.scripts.lua_stdlib import LuaHost


def run(source, version='5.1', **kwargs):
    vm = LuaVM(version=version, **kwargs)
    return vm.execute(source, 'test')


def output(source, version='5.1'):
    vm = LuaVM(version=version)
    vm.execute(source, 'test')
    return vm.host.output


def error_of(source, version='5.1'):
    with unittest.TestCase().assertRaises(LuaError) as caught:
        run(source, version)
    return str(caught.exception)


class TestLanguage(unittest.TestCase):

    def test_arithmetic_and_numbers(self):
        self.assertEqual(run('return 7 % 3, -7 % 3, 7 % -3, 2^10, 10 / 4'),
                         [1, 2, -2, 1024.0, 2.5])
        self.assertEqual(output('print(10 / 2, 1e15, 3.5, 1/0, -1/0, 2^53)'),
                         '5\t1e+15\t3.5\tinf\t-inf\t9.007199254741e+15\n')
        # Lua 5.3 keeps integers and floats apart.
        self.assertEqual(output('print(10 / 2, 7 // 2, 3 | 5, 1 << 4, 2^2)', '5.3'),
                         '5.0\t3\t7\t16\t4.0\n')
        self.assertEqual(run('return "10" + 5, 10 .. 20, "0x10" + 0'), [15, '1020', 16])

    def test_nan_is_not_equal_to_itself(self):
        self.assertEqual(run('local n = 0/0 return n ~= n, n == n'), [True, False])

    def test_closures_capture_each_iteration(self):
        self.assertEqual(run('''
            local t = {}
            for i = 1, 3 do t[i] = function() return i end end
            local k = 0
            while k < 2 do k = k + 1; local j = k; t[3 + k] = function() return j end end
            return t[1](), t[2](), t[3](), t[4](), t[5]()'''), [1, 2, 3, 1, 2])

    def test_upvalues_are_shared(self):
        self.assertEqual(run('''
            local function counter()
                local n = 0
                return function() n = n + 1 return n end, function() return n end
            end
            local inc, get = counter()
            inc() inc()
            return get()'''), [2])

    def test_multiple_results_and_varargs(self):
        self.assertEqual(run('''
            local function f(...) return select('#', ...), ... end
            local function g() return 1, 2, 3 end
            local t = {g(), g()}
            return #t, (g()), f(nil, nil), select(-1, 'a', 'b')'''),
                         [4, 1, 2, 'b'])
        # Only the last expression of a list keeps all its values.
        self.assertEqual(run('local function f(...) return select("#", ...), ... end '
                             'return f(nil, nil)'), [2, None, None])

    def test_local_shadowing_reads_outer_value(self):
        self.assertEqual(run('local x = 1 do local x = x + 1 return x end'), [2])

    def test_goto_continue(self):
        self.assertEqual(output('''
            for i = 1, 4 do
                if i % 2 == 0 then goto continue end
                print(i)
                ::continue::
            end'''), '1\n3\n')

    def test_repeat_condition_sees_body_locals(self):
        self.assertEqual(run('local i = 0 repeat local done = i >= 3; i = i + 1 '
                             'until done return i'), [4])

    def test_tail_calls_do_not_grow_the_stack(self):
        self.assertEqual(run('local function f(n) if n == 0 then return "done" end '
                             'return f(n - 1) end return f(100000)'), ['done'])

    def test_metatables(self):
        self.assertEqual(run('''
            local V = {}
            V.__index = V
            V.__add = function(a, b) return setmetatable({x = a.x + b.x}, V) end
            V.__eq = function(a, b) return a.x == b.x end
            V.__lt = function(a, b) return a.x < b.x end
            V.__tostring = function(v) return "V(" .. v.x .. ")" end
            V.__call = function(v, y) return v.x * y end
            V.__len = function() return 42 end
            function V.new(x) return setmetatable({x = x}, V) end
            function V:double() return V.new(self.x * 2) end
            local a, b = V.new(1), V.new(2)
            return tostring(a + b), a + b == V.new(3), a < b, a(10), a:double().x'''),
            ['V(3)', True, True, 10, 2])

    def test_index_and_newindex_handlers(self):
        self.assertEqual(run('''
            local log = {}
            local proxy = setmetatable({}, {
                __index = function(t, k) return k .. "!" end,
                __newindex = function(t, k, v) rawset(t, k, v * 2); log[#log + 1] = k end})
            proxy.a = 5
            return proxy.a, proxy.b, #log'''), [10, 'b!', 1])

    def test_weak_tables(self):
        self.assertEqual(run('''
            local cache = setmetatable({}, {__mode = "v"})
            cache[1] = {}
            local keep = {}
            cache[2] = keep
            collectgarbage()
            return cache[1] == nil, cache[2] == keep'''), [True, True])

    def test_coroutines(self):
        self.assertEqual(output('''
            local co = coroutine.create(function(a, b)
                local c = coroutine.yield(a + b)
                local d, e = coroutine.yield(c * 2)
                return d + e
            end)
            print(coroutine.resume(co, 1, 2))
            print(coroutine.resume(co, 10))
            print(coroutine.resume(co, 3, 4))
            print(coroutine.status(co), coroutine.resume(co))
            local gen = coroutine.wrap(function() for i = 1, 3 do coroutine.yield(i) end end)
            print(gen(), gen(), gen())'''),
            'true\t3\ntrue\t20\ntrue\t7\ndead\tfalse\tcannot resume dead coroutine\n1\t2\t3\n')

    def test_setfenv_and_getfenv(self):
        self.assertEqual(run('''
            local env = {print = print, x = 1}
            local function f() return x end
            setfenv(f, env)
            x = 99
            return f(), getfenv(f) == env, getfenv(0) == _G'''), [1, True, True])

    def test_lua51_arg_table(self):
        self.assertEqual(run('function f(...) return arg.n, arg[2] end return f(1, 2, 3)'),
                         [3, 2])

    def test_lua51_grammar_is_stricter(self):
        self.assertIn("'<eof>' expected near ';'", error_of('return;;'))
        self.assertIn('ambiguous syntax', error_of('local a = print\n(1)'))
        # Lua 5.1 takes an unknown escape as the character itself.
        self.assertEqual(run(r'return "a\/b"'), ['a/b'])
        self.assertEqual(run('x = 1;; return x', '5.3'), [1])


class TestErrors(unittest.TestCase):

    def test_runtime_errors_name_the_variable(self):
        self.assertEqual(error_of('local t = nil\nreturn t.x'),
                         "test:2: attempt to index local 't' (a nil value)")
        self.assertEqual(error_of('missing()'),
                         "test:1: attempt to call global 'missing' (a nil value)")
        self.assertEqual(error_of('local t = {} return t.a.b'),
                         "test:1: attempt to index field 'a' (a nil value)")
        self.assertEqual(error_of('return 1 + {}'),
                         'test:1: attempt to perform arithmetic on a table value')
        self.assertEqual(error_of('return 1 < "x"'),
                         'test:1: attempt to compare number with string')

    def test_syntax_errors_are_worded_like_lua(self):
        self.assertEqual(error_of('x = = 1'), "test:1: unexpected symbol near '='")
        self.assertEqual(error_of('if x then'), "test:1: 'end' expected near '<eof>'")

    def test_error_levels(self):
        source = '''
            local function inner(level) error("boom", level) end
            local function outer(level) inner(level) end
            return pcall(outer, %d)'''
        self.assertEqual(run(source % 1), [False, 'test:2: boom'])
        self.assertEqual(run(source % 2), [False, 'test:3: boom'])
        self.assertEqual(run(source % 0), [False, 'boom'])
        self.assertEqual(run('return pcall(error, {code = 7})')[1].get('code'), 7)

    def test_xpcall_handler_sees_the_failing_stack(self):
        trace = run('''
            local function deep() error("x") end
            local ok, msg = xpcall(function() deep() end, debug.traceback)
            return msg''')[0]
        self.assertIn('stack traceback:', trace)
        self.assertIn("function 'deep'", trace)

    def test_positions_can_name_other_sources(self):
        vm = LuaVM()
        with self.assertRaises(LuaError) as caught:
            vm.execute('local a = 1\nnope()', 'chunk', positions=['map.j:10', 'map.j:11'])
        self.assertEqual(str(caught.exception),
                         "map.j:11: attempt to call global 'nope' (a nil value)")

    def test_step_budget_stops_endless_loops(self):
        with self.assertRaises(LuaError) as caught:
            run('while true do end', max_steps=10000)
        self.assertIn('steps', str(caught.exception))

    def test_stack_overflow_is_a_lua_error(self):
        self.assertEqual(run_with_stack(run, 'local function f() return 1 + f() end '
                                             'return pcall(f)')[0], False)


class TestLibrary(unittest.TestCase):

    def test_string_functions(self):
        self.assertEqual(run('''return ("hello"):upper(), ("abc"):rep(2, "-"),
            string.sub("hello", 2, -2), string.byte("A"), string.char(72, 105),
            #"\\0ab", ("x"):reverse()'''),
            ['HELLO', 'abc-abc', 'ell', 65, 'Hi', 3, 'x'])

    def test_string_format(self):
        self.assertEqual(run(r'''return string.format("%5.2f|%-4d|%s|%q|%x|%g|%%", 3.14159,
            42, nil, 'a"b\n', 255, 1e20)'''),
            [' 3.14|42  |nil|"a\\"b\\\n"|ff|1e+20|%'])

    def test_patterns(self):
        self.assertEqual(run('return string.find("hello world", "o w")'), [5, 7])
        self.assertEqual(run('return string.find("a.b", ".", 1, true)'), [2, 2])
        self.assertEqual(run('return string.match("key = val", "(%w+)%s*=%s*(%w+)")'),
                         ['key', 'val'])
        self.assertEqual(run('return string.gsub("hello world", "(%w+)", "<%1>")'),
                         ['<hello> <world>', 2])
        self.assertEqual(run('return string.gsub("abc", "%w", {a = 1, b = false})'),
                         ['1bc', 3])
        self.assertIn('invalid replacement value (a boolean)',
                      error_of('string.gsub("abc", "%w", {a = true})'))
        self.assertEqual(run('return string.gsub("f(a(b)c)", "%b()", "")'), ['f', 1])
        self.assertEqual(run('return string.match("THE (quick) fox", "%f[%a]%a+", 5)'),
                         ['quick'])
        self.assertEqual(run('return string.match("2024-01-02", "(%d+)-(%d+)-(%d+)")'),
                         ['2024', '01', '02'])
        self.assertEqual(run('return string.find("abc", "()b()")'), [2, 2, 2, 3])
        self.assertEqual(output('for k, v in string.gmatch("a=1, b=2", "(%w+)=(%w+)") '
                                'do io.write(k, v, ";") end'), 'a1;b2;')
        self.assertIn('malformed pattern', error_of('string.find("a", "[a")'))

    def test_table_functions(self):
        self.assertEqual(run('''
            local t = {5, 2, 8, 1}
            table.sort(t)
            local s = table.concat(t, ",")
            table.sort(t, function(a, b) return a > b end)
            table.insert(t, 1, 0)
            local removed = table.remove(t)
            return s, table.concat(t, ","), removed, table.getn(t), table.maxn({[7] = 1})'''),
            ['1,2,5,8', '0,8,5,2', 1, 4, 7])

    def test_math_functions(self):
        self.assertEqual(run('return math.floor(3.7), math.ceil(3.2), math.max(1, 9, 3), '
                             'math.abs(-4), math.fmod(7, 3), math.sqrt(16)'),
                         [3, 4, 9, 4, 1.0, 4.0])
        first = run('math.randomseed(1) return math.random(1, 100)')
        self.assertEqual(first, run('math.randomseed(1) return math.random(1, 100)'))

    def test_tonumber_and_tostring(self):
        self.assertEqual(run('return tonumber("0x1F"), tonumber(" 12 "), tonumber("z", 36), '
                             'tonumber("1e2"), tonumber("abc"), tostring(nil)'),
                         [31, 12, 35, 100.0, None, 'nil'])

    def test_load_and_loadstring(self):
        self.assertEqual(run('return loadstring("return 1 + 1")(), '
                             'select(2, loadstring("x ="))'),
                         [2, '[string "x ="]:1: unexpected symbol near \'<eof>\''])
        self.assertEqual(run('''
            local parts, i = {"return ", "40", " + 2"}, 0
            return load(function() i = i + 1 return parts[i] end)()'''), [42])

    def test_string_dump_round_trips_through_load(self):
        self.assertEqual(run('local f = loadstring(string.dump(function(a) return a * 2 end)) '
                             'return f(21)'), [42])


class TestSandbox(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        with open(os.path.join(self.root, 'data.txt'), 'wb') as fh:
            fh.write(b'one\r\ntwo\r\n')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_no_file_access_by_default(self):
        self.assertEqual(run('return io.open("x.txt", "w")')[0], None)
        self.assertEqual(run('return os.execute("calc.exe")'), [1])

    def test_reading_and_writing_inside_the_root(self):
        host = LuaHost(root=self.root, writable=True, text_newline='\r\n')
        vm = LuaVM(host=host)
        result = vm.execute('''
            local lines = {}
            for line in io.lines("data.txt") do lines[#lines + 1] = line end
            return lines[1], #lines, io.open("sub/out.txt", "w")''', 'test')
        # Windows text mode: CRLF reads as LF.
        self.assertEqual(result[:3], ['one', 2, None])   # sub/ does not exist yet
        vm.execute(r'os.execute("mkdir sub") local f = io.open("sub/out.txt", "w") '
                   r'f:write("a\nb") f:close() f = io.open("sub/raw.txt", "wb") '
                   r'f:write("a\nb") f:close()', 'test')
        with open(os.path.join(self.root, 'sub', 'out.txt'), 'rb') as fh:
            self.assertEqual(fh.read(), b'a\r\nb')
        with open(os.path.join(self.root, 'sub', 'raw.txt'), 'rb') as fh:
            self.assertEqual(fh.read(), b'a\nb')

    def test_paths_outside_the_root_are_refused(self):
        host = LuaHost(root=self.root, writable=True)
        vm = LuaVM(host=host)
        outside = os.path.join(os.path.dirname(self.root), 'escape.txt')
        result = vm.execute(f'return io.open({outside!r}, "w")', 'test')
        self.assertIsNone(result[0])
        self.assertIn('Permission denied', result[1])
        self.assertTrue(host.denied)
        self.assertFalse(os.path.exists(outside))

    def test_commands_are_interpreted_not_run(self):
        host = LuaHost(root=self.root, writable=True)
        vm = LuaVM(host=host)
        listing = vm.execute('''
            os.execute('if not exist "made" mkdir "made"')
            local names = {}
            for name in io.popen('dir "' .. "." .. '" /b'):lines() do names[#names + 1] = name end
            return table.concat(names, ","), os.execute("calc.exe")''', 'test')
        self.assertEqual(listing, ['data.txt,made', 1])
        self.assertIn(('execute', 'calc.exe'), host.denied)

    def test_mounts_map_absolute_paths(self):
        host = LuaHost(root=self.root, writable=True,
                       mounts={'C:\\Program Files\\Warcraft III': self.root},
                       text_newline='\r\n')
        vm = LuaVM(host=host)
        result = vm.execute('local f = io.open("C:\\\\Program Files\\\\Warcraft III\\\\data.txt") '
                            'return f:read("*l")', 'test')
        self.assertEqual(result, ['one'])

    def test_require_searches_package_path(self):
        with open(os.path.join(self.root, 'mod.lua'), 'w') as fh:
            fh.write('return {answer = 42, name = ...}')
        vm = LuaVM(host=LuaHost(root=self.root))
        self.assertEqual(vm.execute('local m = require "mod" return m.answer, m.name, '
                                    'require "mod" == m', 'test'), [42, 'mod', True])


class TestPythonInterop(unittest.TestCase):

    def test_values_cross_in_both_directions(self):
        vm = LuaVM()
        vm.globals.set('greet', vm.to_lua(lambda name: f'hi {name}'))
        vm.globals.set('config', vm.to_lua({'size': 3, 'tags': ['a', 'b']}))
        result = vm.execute('return greet("you"), config.size, #config.tags, '
                            '{1, 2, {x = "y"}}', 'test')
        self.assertEqual(result[:3], ['hi you', 3, 2])
        self.assertEqual(vm.to_python(result[3]), [1, 2, {'x': 'y'}])

    def test_functions_can_be_called_from_python(self):
        vm = LuaVM()
        add = vm.execute('return function(a, b) return a + b, a * b end', 'test')[0]
        self.assertEqual(add(3, 4), [7, 12])

    def test_text_is_utf8_bytes(self):
        vm = LuaVM()
        self.assertEqual(vm.execute('return #"é", "é"', 'test')[0], 2)
        self.assertEqual(vm.to_python(vm.execute('return "é"', 'test')[0]), 'é')


class TestLua53(unittest.TestCase):
    """Lua 5.3 behaviour, the version Reforged runs; each case comes from a
    failure of the official 5.3.4 test suite."""

    def test_string_pack(self):
        self.assertEqual(run('''
            local s = string.pack(">I2 s1 z i16", 0x1234, "hi", "yo", -3)
            local a, b, c, d, n = string.unpack(">I2 s1 z i16", s)
            return a, b, c, d, n, string.packsize("!i1i8"), #s
        ''', '5.3'), [0x1234, 'hi', 'yo', -3, 25, 16, 24])
        self.assertIn('integer overflow', error_of('string.pack("i1", 200)', '5.3'))
        self.assertIn('variable-length format', error_of('string.packsize("s")', '5.3'))

    def test_env(self):
        self.assertEqual(run('''
            local function f(_ENV) return x end
            local r = f({x = 7})
            do local _ENV = {y = 3}; r = r + y end
            local g = setmetatable({}, {__index = _G})
            local h = load("z = 5; return z", "c", "t", g)
            return r, h(), g.z, rawget(_G, "z") == nil, (debug.getupvalue(h, 1))
        ''', '5.3'), [10, 5, 5, True, '_ENV'])

    def test_env_assignment_redirects_globals(self):
        self.assertEqual(run('''
            X = 20
            _ENV = setmetatable({}, {__index = _G})
            X = X + 10
            return X, _G.X
        ''', '5.3'), [30, 20])

    def test_numerals_and_escapes(self):
        self.assertEqual(run('return 0xF0.0 | 0, 0x.8p4, 0x7fffffffffffffff + 1 == math.mininteger, '
                             '"\\u{48}\\u{e9}"', '5.3'), [240, 8.0, True, 'H\xc3\xa9'])
        self.assertIn("malformed number near '0.23e'", error_of('return 0.23e', '5.3'))
        self.assertIn("near '\"abc\\x\"'", error_of('return "abc\\x"', '5.3'))

    def test_lastmatch_rule(self):
        self.assertEqual(run('return (string.gsub("a b cd", " *", "-"))', '5.3'), ['-a-b-c-d-'])
        self.assertEqual(run('return (string.gsub("a b cd", " *", "-"))', '5.1'), ['-a--b--c-d-'])

    def test_goto_errors(self):
        self.assertIn("jumps into the scope of local 'aa'",
                      error_of('goto l1; local aa ::l1:: ::l2:: print(3)', '5.3'))
        self.assertIn("no visible label 'l1' for <goto> at line 1",
                      error_of('do ::l1:: end goto l1', '5.3'))
        # a label that ends its block is outside the local's scope
        self.assertEqual(run('do goto l1; local a = 1; ::l1:: end return 1', '5.3'), [1])

    def test_messages_and_arithmetic(self):
        self.assertIn("attempt to index a nil value (global 'aaa')",
                      error_of('aaa = nil; return aaa.b', '5.3'))
        self.assertIn("attempt to index global 'aaa' (a nil value)",
                      error_of('aaa = nil; return aaa.b', '5.1'))
        self.assertIn('attempt to divide by zero', error_of('return 2 // 0', '5.3'))
        self.assertIn("attempt to perform 'n%0'", error_of('return 1 % 0', '5.3'))
        self.assertEqual(run('return math.mininteger // -1 == math.mininteger, 0^-1, '
                             'string.format("%+.2A|%a", 12, 1)', '5.3'),
                         [True, float('inf'), '+0X1.80P+3|0x1p+0'])
        self.assertIn('no integer representation', error_of('return "0xffffffffffffffff.0" | 0', '5.3'))

    def test_library_details(self):
        self.assertEqual(run('''
            local main, ismain = coroutine.running()
            return ipairs{} == ipairs{}, type(main), ismain, math.type((math.modf(3))),
                   string.format("%q", math.mininteger), io.flush()
        ''', '5.3'), [True, 'thread', True, 'integer', '0x8000000000000000', True])
        self.assertIn('invalid option', error_of('debug.getinfo(print, "X")', '5.3'))

    def test_utf8_and_table_checks(self):
        self.assertEqual(run('''
            return utf8.len("\\xC0\\x80"), utf8.offset("a\\u{e9}b", 3), utf8.char(72, 233),
                   select(2, pcall(table.move, {}, 1, math.maxinteger, 2)),
                   select(2, pcall(table.unpack, {}, 1, math.maxinteger))
        ''', '5.3'), [None, 4, 'H\xc3\xa9',
                     "bad argument #4 to 'move' (destination wrap around)",
                     'too many results to unpack'])

    def test_closures_and_coroutines(self):
        self.assertEqual(run('''
            local a = {}
            for i = 1, 3 do a[i] = function (x) return x + _ENV.y end end
            local f = function (n) return coroutine.wrap(f)(n) end
            f = function (g) coroutine.wrap(g)(g) end
            local ok, msg = pcall(f, f)
            local yielded = coroutine.wrap(function ()
                 return pcall(table.sort, {1, 2, 3}, coroutine.yield)
            end)()
            return a[1] == a[3], ok, msg:find('C stack overflow') ~= nil, yielded
        ''', '5.3'), [True, False, True, False])


if __name__ == '__main__':
    unittest.main()
