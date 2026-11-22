struct Base
    integer a
    string tag = "base"
    static method create takes integer a, string t returns thistype
        local thistype this = thistype.allocate()
        set this.a = a
        set this.tag = t
        return this
    endmethod
    stub method describe takes nothing returns string
        return tag + I2S(a)
    endmethod
endstruct
struct Child extends Base
    integer b = 5
    method describe takes nothing returns string
        return "child:" + super.describe() + I2S(b)
    endmethod
endstruct
struct GrandChild extends Child
    static method create takes integer a returns thistype
        local thistype this = thistype.allocate(a, "gc")
        set this.b = 99
        return this
    endmethod
endstruct
function test takes nothing returns nothing
    local Base x = Child.create(1, "c")
    local Base y = GrandChild.create(2)
    local Base z = Base.create(3, "z")
    call BJDebugMsg(x.describe() + " " + y.describe() + " " + z.describe())
    call BJDebugMsg(I2S(x.getType()) + I2S(y.getType()) + I2S(z.getType()))
    call x.destroy()
    call y.destroy()
    set x = Child.create(4, "again")
    call BJDebugMsg(I2S(x) + x.describe())
endfunction
function main takes nothing returns nothing
endfunction
