struct D
    method onDestroy takes nothing returns nothing
        call BJDebugMsg("bye")
    endmethod
endstruct
function test takes nothing returns nothing
    local D d = D.create()
    call d.destroy()
    call d.destroy()
    set d = 0
    call d.destroy()
endfunction
function main takes nothing returns nothing
endfunction
