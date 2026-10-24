from enum import Enum


class SoundsFileType(Enum):
     W3S = 'w3s'


# 0x1 looping, 0x2 3D, 0x4 stopWhenOutOfRange, 0x8 music, 0x10 imported/unknown -
# cross-checked against War3Net.Build.Core's SoundFlags.
SOUND_FLAG_BITS = {
     0x01: 'looping',
     0x02: 'is_3d',
     0x04: 'stop_when_out_of_range',
     0x08: 'music',
     0x10: 'unknown_imported',
}
