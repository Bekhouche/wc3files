interface Shape
    method area takes nothing returns real
    method name takes nothing returns string defaults "shape"
endinterface
struct Rect extends Shape
    real w
    real h
    static method create takes real w, real h returns thistype
        local thistype t = thistype.allocate()
        set t.w = w
        set t.h = h
        return t
    endmethod
    method area takes nothing returns real
        return .w * .h
    endmethod
    stub method describe takes nothing returns string
        return "rect"
    endmethod
    method onDestroy takes nothing returns nothing
        call BJDebugMsg("rect gone")
    endmethod
endstruct
struct Square extends Rect
    static method create takes real s returns thistype
        local thistype t = thistype.allocate(s, s)
        return t
    endmethod
    method describe takes nothing returns string
        return "square of " + super.describe()
    endmethod
    method name takes nothing returns string
        return "sq"
    endmethod
    method onDestroy takes nothing returns nothing
        call BJDebugMsg("square gone")
    endmethod
endstruct
function show takes Shape s returns nothing
    call BJDebugMsg(R2S(s.area()) + " " + s.name())
endfunction
function test takes nothing returns nothing
    local Rect r = Rect.create(2, 3)
    local Square q = Square.create(4)
    local Rect asRect = q
    call show(r)
    call show(q)
    call BJDebugMsg(r.describe() + "|" + asRect.describe())
    if q.getType() == Square.typeid then
        call BJDebugMsg("is square")
    endif
    if r.getType() == Rect.typeid then
        call BJDebugMsg("is rect")
    endif
    call asRect.destroy()
    call r.destroy()
endfunction
function main takes nothing returns nothing
endfunction
