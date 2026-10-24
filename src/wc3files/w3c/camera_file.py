import struct
from .data import CameraFileType, BASE_FIELDS, LOCAL_FIELDS, V3_FIELDS
from wc3files.files import BinaryFile


class CameraFile(object):
     """
     Reader/writer for war3map.w3c (saved World Editor cameras).

     Header: `version` (int32, always 0 in every real fixture and every reference
     source) + `count` (int32). Each record is 10 fixed floats (target x/y, zOffset,
     rotation, angleOfAttack, distance, roll, fieldOfView, farClippingPlane,
     nearClippingPlane), *sometimes* followed by 3 more floats (localPitch/Yaw/Roll),
     then a null-terminated `cinematicName` string.

     Two real ambiguities existed between War3Net.Build.Core, HiveWE's
     game_cameras.ixx, mdx-m3-viewer, and ChiefOfGxBxL's spec - both resolved against
     real fixtures rather than picked by source-counting:

     - **Three "local" floats were added to the record without a version bump**
       (confirmed by ChiefOfGxBxL's spec notes and this package's own real-fixture
       check) - `version` staying 0 either way means the file can't self-describe
       which layout it uses. Rather than guess a patch-version cutoff (sources
       disagree: HiveWE says 1.31, mdx-m3-viewer says 1.32) or copy War3Net's
       exception-driven trial-and-fallback, this reader trial-parses the *whole*
       camera list under both layouts and picks whichever one consumes the file
       exactly to the last byte (using the file's own declared `count` as the
       strongest available signal, not an assumption) - confirmed against the 3 real
       fixtures with actual camera data: all 3 parse exactly under the 13-float
       layout, none under the 10-float one.
     - **Field ordering: name before or after the local floats?** mdx-m3-viewer reads
       `cinematicName` *before* the 3 local floats; War3Net, HiveWE, and
       ChiefOfGxBxL all read the name *after*. Checked directly against real bytes:
       reading the name after the local floats (13 floats then name) decodes to
       `"Camera 001"`/`"Camera 002"` - real, human-readable World-Editor-default
       camera names; reading it before (mdx-m3-viewer's order) decodes to an empty
       name every time, which is implausible (cameras are always named by the
       editor). mdx-m3-viewer's ordering is the outlier here; not used.
     """

     def __init__(self):
          self.clear()

     def clear(self):
          self._data = {}

     def read(self, file_path):
          if isinstance(file_path, str):
               file_type = file_path.split('.')[-1].lower()
               if file_type not in [item.value for item in CameraFileType]:
                    raise ValueError(f"Invalid camera file type: {file_type}")

          with BinaryFile(file_path, 'read') as reader:
               # No magic/version-based way to know a record's real size upfront
               # (see class docstring) - read the whole file and trial-parse it,
               # rather than field-by-field through BinaryFile.
               blob = reader.file.read()

          self._data = self._read(blob)

     def write(self, file_path):
          with BinaryFile(file_path, 'write') as writer:
               writer.file.write(self._write(self._data))

     def get_data(self):
          return self._data

     # ---- parse ----------------------------------------------------------------

     @staticmethod
     def _try_parse(blob, count, has_local, version=0):
          # Format 3 (Reforged) carries three further floats before the
          # cinematic name - depth-of-field distance and scale, and an
          # absolute Z position - and a `free_camera` dword after it;
          # format 0 has neither. Both
          # were measured against real maps - a one-camera format 0 record is
          # 63 bytes (13 floats + an 11-byte name), the format 3 equivalents
          # 79 and 88 (16 floats + name + 4).
          extra = V3_FIELDS if version >= 3 else ()
          tail = 4 if version >= 3 else 0
          n_floats = (len(BASE_FIELDS) + (len(LOCAL_FIELDS) if has_local else 0)
                      + len(extra))
          pos = 8
          cameras = []
          for _ in range(count):
               if pos + n_floats * 4 > len(blob):
                    return None
               values = struct.unpack_from(f'<{n_floats}f', blob, pos)
               pos += n_floats * 4

               end = blob.find(b'\x00', pos)
               if end == -1:
                    return None
               try:
                    name = blob[pos:end].decode('utf-8')
               except UnicodeDecodeError:
                    return None
               pos = end + 1

               camera = dict(zip(BASE_FIELDS, values[:len(BASE_FIELDS)]))
               index = len(BASE_FIELDS)
               if has_local:
                    camera.update(dict(zip(LOCAL_FIELDS,
                                           values[index:index + len(LOCAL_FIELDS)])))
                    index += len(LOCAL_FIELDS)
               if extra:
                    camera.update(dict(zip(extra, values[index:])))
               camera['cinematic_name'] = name
               if tail:
                    if pos + tail > len(blob):
                         return None
                    camera['free_camera'] = blob[pos:pos + tail]
                    pos += tail
               cameras.append(camera)

          if pos != len(blob):
               return None
          return cameras

     def _read(self, blob):
          if len(blob) < 8:
               raise ValueError("war3map.w3c file too small to contain a header")

          version, count = struct.unpack_from('<ii', blob, 0)
          if version not in (0, 3):
               raise ValueError(f"Unsupported war3map.w3c version: {version} "
                                f"(only 0 and 3 are implemented)")

          if count == 0:
               return {'version': version, 'has_local_rotation': True, 'cameras': []}

          cameras = self._try_parse(blob, count, has_local=True, version=version)
          has_local = True
          if cameras is None:
               cameras = self._try_parse(blob, count, has_local=False,
                                         version=version)
               has_local = False
          if cameras is None:
               raise ValueError(
                    "Unable to parse war3map.w3c camera records under either the "
                    "10-float or 13-float (with local pitch/yaw/roll) layout - "
                    "declared count doesn't consume the file exactly under either."
               )

          return {'version': version, 'has_local_rotation': has_local, 'cameras': cameras}

     # ---- write --------------------------------------------------------------------

     def _write(self, data):
          version = data.get('version', 0)
          cameras = data.get('cameras', [])
          has_local = data.get('has_local_rotation', True)

          out = bytearray()
          out += struct.pack('<ii', version, len(cameras))
          for camera in cameras:
               values = [camera.get(field, 0.0) for field in BASE_FIELDS]
               if has_local:
                    values += [camera.get(field, 0.0) for field in LOCAL_FIELDS]
               if version >= 3:
                    values += [camera.get(field, 0.0) for field in V3_FIELDS]
               out += struct.pack(f'<{len(values)}f', *values)
               out += camera.get('cinematic_name', '').encode('utf-8')
               out += b'\x00'
               if version >= 3:
                    out += bytes(camera.get('free_camera', bytes(4)))[:4]
          return bytes(out)
