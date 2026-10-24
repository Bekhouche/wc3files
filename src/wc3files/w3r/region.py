import struct
from ..data import BinaryDataType
from typing import Tuple, Dict, Union

class Region:
    def __init__(self,
                 left: float = 0.0,
                 right: float = 0.0,
                 bottom: float = 0.0,
                 top: float = 0.0,
                 name: str = 'region',
                 index: int = 0,
                 weather_effect: bytes = b'\x00\x00\x00\x00',
                 ambient_sound: str = '',
                 color: Tuple[int, int, int] = (255, 255, 255),
                 block_camera: int = 0,
                 alpha_tile_minimap_color: int = 0):
        """
        Initialize a Region object.

        Parameters:
        left (float): Left boundary of the region.
        right (float): Right boundary of the region.
        bottom (float): Bottom boundary of the region.
        top (float): Top boundary of the region.
        name (str): Name of the region.
        index (int): Index of the region.
        weather_effect (bytes): Weather effect for the region as 4-byte data.
        ambient_sound (str): Ambient sound for the region.
        color (Tuple[int, int, int]): RGB color tuple for the region.
        """
        self.left = left
        self.right = right
        self.bottom = bottom
        self.top = top
        self.name = name
        self.index = index
        self.weather_effect = weather_effect
        self.ambient_sound = ambient_sound
        # Region.read() passes the raw 4-byte RGBA value straight from the file;
        # callers constructing a Region directly pass an (R, G, B) tuple per the
        # documented type (and the (255, 255, 255) default) - only the former needs
        # converting.
        if isinstance(color, (bytes, bytearray)):
            self.color = self._bytes_to_color(color)
        else:
            self.color = color
        # Format 7 appends two ints after the colour; the rest of the record
        # is unchanged from format 5. Fitting the file length alone does not
        # pin this down - several arrangements consume exactly the same bytes
        # when the strings are empty and the padding is zeros, and one of them
        # read every colour with alpha 0 and almost every weather id as
        # garbage. The layout here is the one that also makes sense of the
        # contents: across the 14 version-7 files in the benchmark corpus it
        # gives 1247 of 1247 regions a valid weather id and an opaque colour,
        # matching what format 5 does. HiveWE's own wiki page for this format
        # documents the same two fields in the same place and supplies their
        # names, which is where these come from. Both read zero in every
        # region measured here, so the names are documented rather than
        # exercised.
        self.block_camera = block_camera
        self.alpha_tile_minimap_color = alpha_tile_minimap_color

    def get_data(self, version: int = 5) -> Dict[str, Tuple[BinaryDataType, Union[float, str, int, bytes]]]:
        """
        Return a dictionary of the region's data formatted for binary writing.

        Returns:
        Dict[str, Tuple[BinaryDataType, Union[float, str, int, bytes]]]: A dictionary
        mapping each field to its corresponding data type and value.
        """
        fields = {
            'left': (BinaryDataType.REAL, self.left),
            'bottom': (BinaryDataType.REAL, self.bottom),
            'right': (BinaryDataType.REAL, self.right),
            'top': (BinaryDataType.REAL, self.top),
            'name': (BinaryDataType.STRING, self.name),
            'index': (BinaryDataType.INT, self.index),
        }
        fields['weather_effect'] = (BinaryDataType.BYTES, self.weather_effect)
        fields['ambient_sound'] = (BinaryDataType.STRING, self.ambient_sound)
        fields['color'] = (BinaryDataType.BYTES, self._color_to_bytes(self.color))
        if version >= 7:
            fields['block_camera'] = (BinaryDataType.INT, self.block_camera)
            fields['alpha_tile_minimap_color'] = (BinaryDataType.INT, self.alpha_tile_minimap_color)
        return fields

    @staticmethod
    def _bytes_to_color(bytes_val: bytes) -> Tuple[int, int, int]:
        """
        Convert 4-byte RGBA data to a 3-element RGB tuple.

        Parameters:
        bytes_val (bytes): A 4-byte sequence representing an RGBA color.

        Returns:
        Tuple[int, int, int]: An RGB tuple (R, G, B).

        Raises:
        ValueError: If the byte sequence is not exactly 4 bytes long.
        """
        if len(bytes_val) != 4:
            raise ValueError("Expected 4 bytes for color conversion.")
        return struct.unpack('BBB', bytes_val[:3])

    @staticmethod
    def _color_to_bytes(color: Tuple[int, int, int]) -> bytes:
        """
        Convert an RGB tuple to 4-byte RGBA format, adding an alpha channel of 255.

        Parameters:
        color (Tuple[int, int, int]): A tuple representing the RGB values.

        Returns:
        bytes: A 4-byte sequence representing an RGBA color.
        """
        if len(color) != 3 or not all(0 <= val <= 255 for val in color):
            raise ValueError("Color must be a tuple of three integers between 0 and 255.")
        return bytes(color) + b'\xff'

    @staticmethod
    def structure(version: int = 5) -> Dict[str, BinaryDataType]:
        """
        Define the binary structure for this class, mapping field names to data types.

        Dict order is the field order on disk, so the two format-7 fields are
        inserted at their real positions rather than appended.

        Returns:
        Dict[str, BinaryDataType]: A dictionary that maps field names to their binary data types.
        """
        fields = {
            'left': BinaryDataType.REAL,
            'bottom': BinaryDataType.REAL,
            'right': BinaryDataType.REAL,
            'top': BinaryDataType.REAL,
            'name': BinaryDataType.STRING,
            'index': BinaryDataType.INT,
        }
        fields['weather_effect'] = BinaryDataType.BYTES
        fields['ambient_sound'] = BinaryDataType.STRING
        fields['color'] = BinaryDataType.BYTES
        if version >= 7:
            fields['block_camera'] = BinaryDataType.INT
            fields['alpha_tile_minimap_color'] = BinaryDataType.INT
        return fields

    def __repr__(self) -> str:
        """Provide a string representation of the Region object for easier debugging."""
        return (f"<Region {self.index}: {self.name}, "
                f"Bounds: (left: {self.left}, right: {self.right}, "
                f"bottom: {self.bottom}, top: {self.top}), "
                f"Weather Effect: {self.weather_effect.hex()}, "
                f"Ambient Sound: '{self.ambient_sound}', "
                f"Color: RGB{self.color}>")
