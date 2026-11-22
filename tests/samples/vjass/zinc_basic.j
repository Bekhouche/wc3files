//! zinc
library ZTest requires ZDep {
    integer counter = 0;
    constant integer LIMIT = 5;
    integer arr[];
    type Op extends function(integer, integer) -> integer;
    type ints extends integer[4];

    function add(integer a, integer b) -> integer { return a + b; }

    public function show(string s) {
        BJDebugMsg(s);
    }

    struct Point {
        real x, y = 2.0;
        static integer made = 0;
        static method create(real x) -> thistype {
            thistype p = thistype.allocate();
            p.x = x;
            made += 1;
            return p;
        }
        method sum() -> real { return x + y; }
        method operator < (thistype o) -> boolean { return this.x < o.x; }
    }

    interface Animal {
        method speak() -> string;
        method legs() -> integer = 4;
    }
    struct Dog extends Animal {
        method speak() -> string { return "woof"; }
    }
    struct Bird extends Animal {
        method speak() -> string { return "tweet"; }
        method legs() -> integer { return 2; }
    }

    public function run() {
        integer i, j = 3, total = 0;
        Op f = add;
        Point p = Point.create(1.5);
        Point q = Point.create(3.0);
        Animal a = Dog.create();
        Animal b = Bird.create();
        ints d = ints.create();
        for (0 <= i < LIMIT) { total += i; }
        show("for " + I2S(total));
        for (LIMIT > i >= 0) { total -= 1; }
        show("down " + I2S(total) + " " + I2S(i));
        i = 0;
        while (i < 3) { i += 1; if (i == 2) break; }
        show("while " + I2S(i));
        do { i = i * 2; } while (i < 20);
        show("do " + I2S(i));
        for (i = 0, j = 10; i < j; i += 2, j -= 2) { counter += 1; }
        show("cfor " + I2S(counter));
        show(I2S(f.evaluate(2, 3)) + " " + R2S(p.sum()) + " " + I2S(Point.made));
        if (p < q && !(q < p)) { show("ordered"); } else { show("not"); }
        if (!true || 1 > 2) { show("bad"); } else if (j == 6) { show("j6"); } else { show("else"); }
        show(a.speak() + I2S(a.legs()) + b.speak() + I2S(b.legs()));
        d[3] = 7; arr[2] = d[3] * 2;
        show(I2S(2:arr) + " " + I2S(-arr[2] + 1));
        i = 5; i *= 3;
        show("compound " + I2S(i));
        debug show("debug!");
        static if (LIBRARY_ZDep && !LIBRARY_Missing) { show("static ok"); }
        TimerStart(CreateTimer(), 0, false, function() { show("anon"); });
    }
    function onInit() { show("zinit"); }
}
library ZDep { public function ok() {} }
//! endzinc
function test takes nothing returns nothing
    call run()
endfunction
function main takes nothing returns nothing
endfunction
