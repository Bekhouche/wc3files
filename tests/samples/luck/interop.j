// Plain JASS can live beside Luck. JASS functions are discoverable by the Luck IDE
// and can be imported from Luck using the source-file path.
function JassMultiply takes integer a, integer b returns integer
    return a * b
endfunction

// Public Luck names are stable, so JASS can call them directly. The final JASS
// normalizer orders Greet before this caller even if the source file comes first.
function CallLuckFromJass takes nothing returns nothing
    call Greet("called from JASS")
endfunction

// A file can switch languages with //! language. The Luck island below is part
// of this same interop.j file and can itself use imports.
//! language luck
from "examples/functions.luck" import Greet

public nothing LuckInsideJassFile():
    Greet(name = "Luck island inside interop.j")
