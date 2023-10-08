import sys
from loguru import logger
sys.path.append('./src')
from wc3files import ObjectFile

w3u_input = 'E:/warcraft/my_tools/data/random/unit.w3u'
w3u_output = 'E:/warcraft/my_tools/data/random/unit_out.w3u'

w3u = ObjectFile()
w3u.read(w3u_input)
w3u.write(w3u_output)