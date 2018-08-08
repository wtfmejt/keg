import struct
from binascii import hexlify
from io import BytesIO
from os import SEEK_CUR, SEEK_END
from typing import IO, Iterable, List, Optional

from .blte import BLTEDecoder


class Archive:
	def __init__(self, key: str, cdn) -> None:
		self.key = key
		self.cdn = cdn
		self._data: Optional[IO] = None

	def __repr__(self):
		return f"<{self.__class__.__name__}: {self.key}>"

	def __del__(self):
		if self._data is not None:
			self._data.close()

	def get_file_data(self, size: int, offset: int):
		if self._data is None:
			self._data = self.cdn.download_data(self.key)

		assert self._data
		self._data.seek(offset)
		data = self._data.read(size)
		return data

	def get_file(self, key: str, size: int, offset: int, verify: bool=False):
		data = self.get_file_data(size, offset)
		decoded_data = BLTEDecoder(BytesIO(data), key, verify=verify)
		return b"".join(decoded_data.blocks)


class ArchiveIndex:
	def __init__(self, data: bytes, key: str, verify: bool=False) -> None:
		self.key = key
		self.verify = verify

		self.data = BytesIO(data)
		self.data.seek(-28, SEEK_END)

		(
			toc_hash,
			version,
			_,
			_,
			self.block_size_kb,
			self.offset_size,
			self.size_size,
			self.key_size,
			checksum_size,
			self.num_items,
			footer_checksum
		) = struct.unpack("<8s8BI8s", self.data.read())

	def __repr__(self):
		return f"<{self.__class__.__name__}: {self.key}>"

	@property
	def items(self):
		self.data.seek(0)

		bytes_left_in_block = self.block_size_kb * 1024

		for i in range(self.num_items):
			bytes_to_read = self.key_size + self.size_size + self.offset_size
			if bytes_to_read > bytes_left_in_block:
				self.data.seek(bytes_left_in_block, SEEK_CUR)
				bytes_left_in_block = self.block_size_kb * 1024
			bytes_left_in_block -= bytes_to_read

			_data = self.data.read(bytes_to_read)
			key, size, offset = struct.unpack(">16sII", _data)
			key = hexlify(key).decode()
			yield key, size, offset


class ArchiveGroupIndex:
	def __init__(
		self, archive_indices: Iterable[ArchiveIndex], key: str, verify: bool=False
	) -> None:
		# sort keys in all indices and write them to blocks
		# write toc from last hash in blocks and md5sum(block_data)
		# write footer from md5sum(toc) and with md5sum(footer)
		# verify archive name against md5sum(toc_hash + footer)

		self.archive_indices = archive_indices
		self.key = key
		self.items = sorted(
			(key, size, archive_id, offset)
			for archive_id, archive_index in enumerate(self.archive_indices)
			for key, size, offset in archive_index.items
		)
		# Keep a copy of all the item keys for efficient retrieval of loose files
		self.item_keys = set(k[0] for k in self.items)

		# TODO: write to disk /impl write method, then verify

	def __repr__(self):
		return f"<{self.__class__.__name__}: {self.key}>"


class ArchiveGroup:
	def __init__(self, archive_keys: List[str], key: str, cdn, verify: bool=False) -> None:
		self.archive_keys = archive_keys
		self.key = key
		self.cdn = cdn
		self.verify = verify

		self.archives: List[Archive] = [
			Archive(archive_key, cdn) for archive_key in archive_keys
		]

	def __repr__(self):
		return f"<{self.__class__.__name__}: {self.key}>"

	@property
	def files(self):
		for file_info in self.merged_index.items:
			yield self.get_file(*file_info)

	@property
	def indices(self) -> Iterable[ArchiveIndex]:
		for archive_key in self.archive_keys:
			yield self.cdn.download_data_index(archive_key, verify=self.verify)

	@property
	def merged_index(self) -> ArchiveGroupIndex:
		return ArchiveGroupIndex(
			self.indices, self.key, verify=self.verify
		)

	def get_file(self, key: str, size: int, archive_id: int, offset: int):
		return self.archives[archive_id].get_file(key, size, offset)
