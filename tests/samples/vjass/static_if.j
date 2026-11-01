library Opt
endlibrary
library User requires optional Opt, optional Nope
    globals
        constant boolean FLAG = true
    endglobals
    function f takes nothing returns nothing
        static if LIBRARY_Opt then
            call BJDebugMsg("opt")
        endif
        static if LIBRARY_Nope then
            call BJDebugMsg("nope")
        else
            call BJDebugMsg("no nope")
        endif
        static if FLAG and not DEBUG_MODE then
            call BJDebugMsg("flag")
        endif
        debug call BJDebugMsg("debug only")
    endfunction
endlibrary
function test takes nothing returns nothing
    call f()
endfunction
function main takes nothing returns nothing
endfunction
