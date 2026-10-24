from enum import Enum


class CameraFileType(Enum):
     W3C = 'w3c'


BASE_FIELDS = (
     'target_x', 'target_y', 'z_offset', 'rotation', 'angle_of_attack',
     'distance', 'roll', 'field_of_view', 'far_clipping_plane', 'near_clipping_plane',
)
LOCAL_FIELDS = ('local_pitch', 'local_yaw', 'local_roll')
# Format 3 (Reforged) adds three floats before the cinematic name and a
# `free_camera` dword after it. The three were carried as unknowns while the
# layout was derived from byte counts here; the names come from HiveWE's
# parser, which reads the same fields in the same order.
V3_FIELDS = ('dof_distance', 'dof_scale', 'pos_absolute_z')
