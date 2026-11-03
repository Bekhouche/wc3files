"""
FileImporter's auto-conversion, as the Grim Extension Pack manual describes
it.

- An image (.bmp, .tga, also .png) imported as .blp is converted to BLP.
  A .jpg is imported as it is: this package's JPEG decoder reads the
  planes inside BLP files, not ordinary JPEG files; the manual's own rule
  for a conversion that fails is to import the original file under its own
  path, and that is what happens.
- A .mdl imported as .mdx is converted to MDX.
- An icon whose file name starts with CMD, PAS or AUT (in any combination)
  is resized to 64x64, given the normal, passive or autocast border, written
  to ReplaceableTextures\\CommandButtons, PassiveButtons or AutocastButtons,
  and gets a disabled version in ReplaceableTextures\\CommandButtonsDisabled
  (DIS + its name), shared when the prefixes are combined.
- infocard- and level-infocard- images get the attack/armor or the caster
  upgrade border and keep their imported path.

The borders are the classic ones this package bundles (wc3files.img.borders),
not the TGA files of a Grim Extension Pack install; BLP output is the
paletted kind (wc3files.img.BlpFile writes no JPEG).

`convert(source_name, data, archive)` returns [(archive path, bytes), ...],
or None when the file is imported as it is.
"""

import os
import tempfile

_IMAGE = ('.bmp', '.tga', '.png')
_ICON_FOLDERS = {'CMD': 'ReplaceableTextures\\CommandButtons\\',
                 'PAS': 'ReplaceableTextures\\PassiveButtons\\',
                 'AUT': 'ReplaceableTextures\\AutocastButtons\\'}
_BORDER_OF = {'CMD': 'active', 'PAS': 'passive', 'AUT': 'autocast'}


def _temp(data, suffix):
     handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
     handle.write(data)
     handle.close()
     return handle.name


def _read_image(data, suffix):
     from wc3files.img import BmpFile, TgaFile, read_png
     if suffix == '.png':
          return read_png(data)
     image = BmpFile() if suffix == '.bmp' else TgaFile()
     image.read(data)
     return image.width, image.height, image.get_rgba()


def _blp(width, height, rgba):
     from wc3files.img import BlpFile
     return BlpFile.from_rgba(width, height, rgba).to_bytes()


def _border(kind):
     from wc3files.img import read_png
     from wc3files.img.borders import border_path
     return read_png(border_path(kind))


def _framed(rgba, width, height, kind):
     from wc3files.img.transform import resize_box, composite_over
     if (width, height) != (64, 64):
          rgba = resize_box(rgba, width, height, 64, 64)
     bw, bh, border = _border(kind)
     return composite_over(rgba, 64, 64, border, bw, bh)


def icon_prefixes(base):
     """The CMD/PAS/AUT prefixes at the start of a file name, and the rest."""
     kinds = []
     rest = base
     while rest[:3].upper() in _ICON_FOLDERS and rest[:3].upper() not in kinds:
          kinds.append(rest[:3].upper())
          rest = rest[3:]
     return kinds, rest


def _mdx(data):
     from wc3files.mdl import ModelFile
     source = _temp(data, '.mdl')
     target = source[:-4] + '.mdx'
     try:
          model = ModelFile()
          model.read(source)
          model.write(target)
          with open(target, 'rb') as handle:
               return handle.read()
     finally:
          for path in (source, target):
               if os.path.exists(path):
                    os.remove(path)


def convert(source_name, data, archive):
     """The files to import for `data` (read from `source_name`) asked for at
     `archive`, or None to import it as it is."""
     source_ext = os.path.splitext(source_name)[1].lower()
     target_ext = os.path.splitext(archive)[1].lower()
     try:
          if source_ext == '.mdl' and target_ext == '.mdx':
               return [(archive, _mdx(data))]
          if source_ext not in _IMAGE or target_ext != '.blp':
               return None
          width, height, rgba = _read_image(data, source_ext)
     except Exception:
          return None                     # imported as it is, as the manual says
     base = os.path.basename(source_name)
     kinds, rest = icon_prefixes(base)
     if kinds:
          from wc3files.img.transform import make_disabled_variant, resize_box
          name = os.path.splitext(rest)[0] + '.blp'
          out = [(_ICON_FOLDERS[k] + name, _blp(64, 64, _framed(rgba, width, height,
                                                                 _BORDER_OF[k])))
                 for k in kinds]
          plain = rgba if (width, height) == (64, 64) else resize_box(rgba, width, height, 64, 64)
          disabled = make_disabled_variant(plain, 64, 64)
          out.append(('ReplaceableTextures\\CommandButtonsDisabled\\DIS' + name,
                      _blp(64, 64, disabled)))
          return out
     lower = base.lower()
     if lower.startswith('level-infocard-'):
          return [(archive, _blp(64, 64, _framed(rgba, width, height, 'upgrade')))]
     if lower.startswith('infocard-'):
          return [(archive, _blp(64, 64, _framed(rgba, width, height, 'attack')))]
     return [(archive, _blp(width, height, rgba))]
