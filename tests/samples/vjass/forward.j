struct S
    integer v
    method a takes integer x returns integer
        return this.b(x) + 1
    endmethod
    method b takes integer x returns integer
        return x * 2 + .v
    endmethod
    static method early takes nothing returns nothing
        call BJDebugMsg("early " + I2S(thistype.late(3)))
    endmethod
    static method late takes integer q returns integer
        call BJDebugMsg("late")
        return q + 100
    endmethod
endstruct
function hookme takes string s returns nothing
    call DisplayTextToPlayer(GetLocalPlayer(), 0, 0, "hooked " + s)
endfunction
hook BJDebugMsg hookme
function test takes nothing returns nothing
    local S s = S.create()
    set s.v = 10
    call BJDebugMsg(I2S(s.a(5)))
    call S.early()
    call S.late.execute(1)
endfunction
function main takes nothing returns nothing
endfunction
