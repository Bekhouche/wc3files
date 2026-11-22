library_once Once
    function once takes nothing returns string
        return "first"
    endfunction
endlibrary
library_once Once
    function once takes nothing returns string
        return "second"
    endfunction
endlibrary
scope Outer
    private keyword helper
    scope Inner
        public function f takes nothing returns string
            return "inner " + SCOPE_PREFIX
        endfunction
    endscope
    private function helper takes nothing returns string
        return Inner_f()
    endfunction
    public function g takes nothing returns string
        return helper() + " outer"
    endfunction
endscope
struct Ops extends array
    static method operator [] takes integer i returns integer
        return i + 1000
    endmethod
    static method operator count takes nothing returns integer
        return 77
    endmethod
endstruct
struct Inner2
    integer val = 1
endstruct
struct Wrap
    delegate Inner2 inner
    static method create takes nothing returns thistype
        local thistype t = thistype.allocate()
        set t.inner = Inner2.create()
        return t
    endmethod
endstruct
function test takes nothing returns nothing
    local Wrap w = Wrap.create()
    set w.val = 9
    call BJDebugMsg(once() + " " + Outer_g() + " " + Outer_Inner_f())
    call BJDebugMsg(I2S(Ops[5]) + " " + I2S(Ops.count) + " " + I2S(w.val) + " " + I2S(w.inner.val))
endfunction
function main takes nothing returns nothing
endfunction
