import sys
from loguru import logger
sys.path.append('./src')
from wc3files import RegionFile

w3r_input = 'E:/warcraft/maps/footmen_frenzy/0.1.w3x/war3map.w3r'
w3r_output = './outputs/war3map.w3r'


w3r = RegionFile()
w3r.read(w3r_input)
w3r.write(w3r_output)