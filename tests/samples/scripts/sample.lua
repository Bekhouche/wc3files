local Foo = {};
Foo.__index = Foo;
function Foo.new(x, y)
    local self = setmetatable({}, Foo);
    self.x = x;
    self.y = y;
    return self;
end;
function Foo:magnitude()
    return (((self.x ^ 2) + (self.y ^ 2)) ^ 0.5);
end;
local function sum(...)
    local total = 0;
    local args = {...};
    for i = 1, # args do
        total = (total + args[i]);
    end;
    return total;
end;
local function classify(n)
    if (n < 0) then
        return "negative";
    elseif (n == 0) then
        return "zero";
    else
        return "positive";
    end;
end;
local t = {1, 2, 3, name = "test", [10] = "ten", nested = {a = 1, b = 2}};
local i = 0;
while (i < 3) do
    i = (i + 1);
end;
repeat
    i = (i - 1);
until (i <= 0);
for k, v in pairs(t) do
    if (type(v) == "string") then
        print(k, v);
    end;
end;
do
    local scoped = "block-scoped";
end;
local function firstPositive(list)
    for _, v in ipairs(list) do
        if (v > 0) then
            return v;
        end;
    end;
    return nil;
end;
local mask = 255;
local flags = ((mask & 15) | (mask >> 4));
local notMask = ~ mask;
local floorDiv = (7 // 2);
local greeting = "hello \"world\"\nsecond line";
local block = "raw block string\nwith a \"quote\" and no escapes\n";
local callback = function(a, b)
    return (a .. tostring(b));
end;
print(callback("value: ", 42));
print(sum(1, 2, 3, 4));
print(classify(- 5), classify(0), classify(5));
