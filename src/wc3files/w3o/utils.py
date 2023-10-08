

def DEC2ID(number, pre=None, num=4):
     digits = "0123456789abcdefghijklmnopqrstuvwxyz".upper()

     code = ""
     number = abs(number)

     while number > 0:
          remainder = number % 36
          code = digits[remainder] + code
          number //= 36
     code = code.zfill(num)
     if pre:
          code = pre + code
     return code

