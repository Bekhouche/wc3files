module Counter
    private static integer made = 0
    static method made_count takes nothing returns integer
        return thistype.made
    endmethod
    method track takes nothing returns nothing
        set made = made + 1
    endmethod
    private static method onInit takes nothing returns nothing
        call BJDebugMsg("module init " + I2S(thistype.typeid))
    endmethod
endmodule
struct A
    implement Counter
    static method onInit takes nothing returns nothing
        call BJDebugMsg("struct init A")
    endmethod
endstruct
struct D
    delegate A inner
    static method create takes nothing returns thistype
        local thistype t = thistype.allocate()
        set t.inner = A.create()
        return t
    endmethod
endstruct
function test takes nothing returns nothing
    local D d = D.create()
    call d.track()
    call d.track()
    call BJDebugMsg(I2S(A.made_count()))
endfunction
function main takes nothing returns nothing
endfunction
