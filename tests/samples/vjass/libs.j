library C requires B
    function cf takes nothing returns nothing
        call BJDebugMsg("C uses " + B_name())
    endfunction
endlibrary
library B initializer init requires A
    globals
        private integer n = 7
    endglobals
    public function name takes nothing returns string
        return "B" + I2S(n) + A_x()
    endfunction
    private function init takes nothing returns nothing
        call BJDebugMsg("init B")
    endfunction
endlibrary
library A initializer init
    public function x takes nothing returns string
        return SCOPE_PREFIX
    endfunction
    private function init takes nothing returns nothing
        call BJDebugMsg("init A")
    endfunction
endlibrary
scope S initializer go
    private function go takes nothing returns nothing
        call BJDebugMsg("scope init")
        call cf()
    endfunction
endscope
function test takes nothing returns nothing
    call BJDebugMsg("main")
endfunction
function main takes nothing returns nothing
endfunction
