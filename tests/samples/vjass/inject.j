library Lib initializer li
    private function li takes nothing returns nothing
        call BJDebugMsg("lib init")
    endfunction
endlibrary
struct Q
    static method onInit takes nothing returns nothing
        call BJDebugMsg("Q init")
    endmethod
endstruct
//! inject main
    call BJDebugMsg("before")
    //! dovjassinit
    call BJDebugMsg("after")
//! endinject
function test takes nothing returns nothing
endfunction
function main takes nothing returns nothing
    call BJDebugMsg("original main")
endfunction
