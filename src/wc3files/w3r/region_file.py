import struct
from ..data import BinaryDataType
from .data import RegionFileType
from .region import Region
from wc3files.files import BinaryFile
from typing import Dict

class RegionFile:
    def __init__(self):
        """
        Initialize the RegionFile object and clear its data.
        """
        self.clear()
    
    def clear(self):
        """
        Clear the RegionFile data, resetting version and region count.
        """
        self._data: Dict[int, Region] = {}
        self.version: int = 5
        self.number: int = 0

    def read(self, file_path: str):
        """
        Read region data from a binary file.

        Parameters:
        file_path (str): The path to the region file to read.

        Raises:
        ValueError: If the file extension is not a valid region file type.
        """
        file_type = file_path.split('.')[-1].lower()

        # Check if the file type is valid for a region file
        if file_type not in [item.value for item in RegionFileType]:
            raise ValueError(f"Invalid region file type: {file_type}")

        # Read the file using BinaryFile reader
        with BinaryFile(file_path, 'read') as reader:
            self.version = reader.read(BinaryDataType.INT)
            self.number = reader.read(BinaryDataType.INT)
            self._data.clear()

            for idx in range(self.number):
                data = reader.read_batch(Region.structure(self.version))
                region = Region(**data)
                self._data[idx] = region

    def write(self, file_path: str):
        """
        Write region data to a binary file.

        Parameters:
        file_path (str): The path to the region file to write.

        Raises:
        ValueError: If the file extension is not a valid region file type.
        """
        file_type = file_path.split('.')[-1].lower()

        # Check if the file type is valid for a region file
        if file_type not in [item.value for item in RegionFileType]:
            raise ValueError(f"Invalid region file type: {file_type}")

        # Write to the file using BinaryFile writer
        with BinaryFile(file_path, 'write') as writer:
            writer.write(BinaryDataType.INT, self.version)
            writer.write(BinaryDataType.INT, len(self._data))
            
            for region in self._data.values():
                writer.write_batch(region.get_data(self.version))

    def get_regions(self) -> Dict[int, Region]:
        """
        Retrieve the region data stored in this file.

        Returns:
        Dict[int, Region]: A dictionary mapping region indices to Region objects.
        """
        return self._data

    def add_region(self, index: int, region: Region):
        """
        Add a region to the RegionFile.

        Parameters:
        index (int): The index to associate with the region.
        region (Region): The region object to add.
        """
        self._data[index] = region
        self.number = len(self._data)

    def remove_region(self, index: int):
        """
        Remove a region from the RegionFile by index.

        Parameters:
        index (int): The index of the region to remove.

        Raises:
        KeyError: If the region index does not exist.
        """
        if index in self._data:
            del self._data[index]
            self.number = len(self._data)
        else:
            raise KeyError(f"No region found at index {index}")

    def __repr__(self) -> str:
        """Return a string representation of the RegionFile object."""
        return (f"<RegionFile version={self.version}, "
                f"number_of_regions={self.number}, "
                f"regions={list(self._data.keys())}>")
