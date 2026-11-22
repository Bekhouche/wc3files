library L
    globals
        key K1
        key K2
        integer array big[20]
        integer array grid[3][4]
    endglobals
    function interface Op takes integer a, integer b returns integer
    function add takes integer a, integer b returns integer
        return a + b
    endfunction
    function mul takes integer a, integer b returns integer
        return a * b
    endfunction
    type ints extends integer array [5]
    struct Vec extends array
        method operator [] takes integer i returns integer
            return this * 100 + i
        endmethod
        method operator x takes nothing returns integer
            return integer(this) + 1
        endmethod
        method operator x= takes integer v returns nothing
            call BJDebugMsg("set x " + I2S(v))
        endmethod
        method operator < takes thistype o returns boolean
            return integer(this) < integer(o)
        endmethod
    endstruct
endlibrary
function test takes nothing returns nothing
    local Op f = Op.add
    local ints a = ints.create()
    local ints b = ints.create()
    local Vec v = 3
    set a[4] = 9
    set b[0] = 1
    call BJDebugMsg(I2S(f.evaluate(3, 4)) + " " + I2S(mul.evaluate(3, 4)))
    set f = Op.mul
    call BJDebugMsg(I2S(f.evaluate(5, 6)) + " " + I2S(a[4]) + " " + I2S(b[0]) + " " + I2S(ints.size))
    call BJDebugMsg(I2S(v[7]) + " " + I2S(v.x))
    set v.x = 42
    if v < Vec(4) then
        call BJDebugMsg("less")
    endif
    if not (v > Vec(4)) then
        call BJDebugMsg("not greater")
    endif
    set grid[2][3] = 5
    set big[19] = 1
    call BJDebugMsg(I2S(grid[2][3]) + I2S(grid.width) + I2S(grid.height) + I2S(big.size))
    call BJDebugMsg(add.name)
    call a.destroy()
    if K1 != K2 then
        call BJDebugMsg("keys differ")
    endif
endfunction
function main takes nothing returns nothing
endfunction
