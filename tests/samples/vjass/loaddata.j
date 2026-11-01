struct Item extends array
    integer cost
    string label
    static method getFromKey takes integer id returns thistype
        return id - 'I000' + 1
    endmethod
endstruct
//! loaddata "items.slk"
function test takes nothing returns nothing
    call BJDebugMsg(Item(1).label + I2S(Item(1).cost) + Item(2).label + I2S(Item(2).cost))
endfunction
function main takes nothing returns nothing
endfunction
