interface Speaker
    method say takes string s returns nothing
    method hear takes nothing returns nothing defaults nothing
    static method create takes nothing
endinterface
struct Loud extends Speaker
    method say takes string s returns nothing
        call BJDebugMsg("LOUD " + s)
    endmethod
endstruct
struct Quiet extends Speaker
    method say takes string s returns nothing
        call BJDebugMsg("quiet " + s)
    endmethod
    method hear takes nothing returns nothing
        call BJDebugMsg("quiet hears")
    endmethod
endstruct
struct Plain
    integer n = 3
    method twice takes integer x returns integer
        return x * 2 + .n
    endmethod
    static method sq takes integer x returns integer
        return x * x
    endmethod
endstruct
function test takes nothing returns nothing
    local Speaker a = Speaker.create(Loud.typeid)
    local Speaker b = Speaker.create(Quiet.typeid)
    local Plain p = Plain.create()
    call a.say("hi")
    call b.say.execute("there")
    if a.hear.exists then
        call BJDebugMsg("a hears")
    else
        call BJDebugMsg("a deaf")
    endif
    if b.hear.exists then
        call b.hear()
    endif
    call a.hear()
    call BJDebugMsg(I2S(p.twice.evaluate(5)) + " " + I2S(Plain.sq.evaluate(4)))
    call p.twice.execute(1)
    call BJDebugMsg(Plain.sq.name + " " + p.twice.name)
endfunction
function main takes nothing returns nothing
endfunction
