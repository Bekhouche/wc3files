globals
    integer udg_Count = 0
    unit array udg_Units
    constant real PI = 3.14
endglobals

function Foo takes integer a, unit u returns boolean
    local integer i = 0
    local real life
    set udg_Count = udg_Count + 1
    set udg_Units[i] = u
    if (i > 0 and not (i == 5)) then
        call BJDebugMsg("hi" + I2S(i))
    elseif i < 0 then
        return false
    else
        debug call BJDebugMsg("else")
    endif
    loop
        exitwhen i >= 10
        set i = i + 1
    endloop
    call TriggerAddCondition(CreateTrigger(), Condition(function Foo))
    return true
endfunction
