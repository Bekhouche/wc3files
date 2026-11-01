globals
    integer array huge[20000]
    integer array wide[100][300]
endglobals
struct Many[20000]
    integer v
endstruct
type longs extends integer array [10, 30000]
function test takes nothing returns nothing
    local integer i = 0
    local Many m
    local longs l
    loop
        exitwhen i > 9000
        set m = Many.create()
        set m.v = i
        set i = i + 1
    endloop
    call BJDebugMsg(I2S(m) + " " + I2S(m.v))
    set huge[19999] = 7
    set huge[3] = 8
    set wide[99][299] = 5
    call BJDebugMsg(I2S(huge[19999] + huge[3]) + " " + I2S(wide[99][299]) + " " + I2S(huge.size) + " " + I2S(wide.width) + " " + I2S(wide.height))
    set i = 0
    loop
        exitwhen i > 1000
        set l = longs.create()
        set i = i + 1
    endloop
    set l[9] = 42
    call BJDebugMsg(I2S(l) + " " + I2S(l[9]) + " " + I2S(l.size))
endfunction
function main takes nothing returns nothing
endfunction
