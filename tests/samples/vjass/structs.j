struct P
    integer a = 5
    real r
    static integer count = 0
    integer array arr[3]
    static method create takes integer x returns thistype
        local thistype t = thistype.allocate()
        set t.r = x * 1.5
        set count = count + 1
        set t.arr[2] = x
        return t
    endmethod
    method sum takes nothing returns integer
        return .a + .arr[2] + R2I(this.r)
    endmethod
    method onDestroy takes nothing returns nothing
        set count = count - 1
        call BJDebugMsg("destroy " + I2S(.a))
    endmethod
endstruct
struct Plain
    string s = "hi"
endstruct
function test takes nothing returns nothing
    local P p = P.create(4)
    local P q = P.create(10)
    local Plain z = Plain.create()
    call BJDebugMsg(I2S(p.sum()) + " " + I2S(q.sum()) + " " + I2S(P.count))
    call BJDebugMsg(z.s + I2S(integer(q)))
    call p.destroy()
    call BJDebugMsg(I2S(P.count))
    set p = P.create(1)
    call BJDebugMsg(I2S(p) + " " + I2S(p.arr[2]))
    call z.destroy()
endfunction
function main takes nothing returns nothing
endfunction
