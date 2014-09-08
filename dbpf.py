#!/usr/bin/python3
import struct
import sys
import pprint
import zlib

from collections import namedtuple

class FormatException(Exception):
    pass

class ResourceType:
    DATA = 0x545AC67A
    
class ResourceID(namedtuple("ResourceID", "group instance type")):
    """A DBPF resource ID. This can be used directly as a RID filter; it matches only itself"""
    __slots__ = ()
    def __repr__(self):
        return "%08x!%016x.%08x" % (self.group, self.instance, self.type)

    def match(self, rid):
        """Determine if this RID is the same as the argument. Strictly speaking,
        this is the same as __eq__, but ResourceFilters must implement this function"""
        return rid == self

    @property
    def deleted(self):
        return self.compression[0] == 0xFFE0
        

# TODO: also store a reference to the DBPF that this entry is from
IndexEntry = namedtuple("IndexEntry", 'id offset size size_decompressed compression')
            
class ResourceFilter:
    """A simple resource filter; this matches iff all specified RID
    components are equal to the ones in the RID being tested"""
    
    def __init__(self, group=None,instance=None,type=None):
        self.group = group
        self.instance = instance
        self.type = type

    def match(self, rid):
        return ((self.group is None or self.group == rid.group) and
                (self.instance is None or self.instance == rid.instance) and
                (self.type is None or self.type == rid.type))


foo = [None]
class DBPFFile:
    """A Sims4 DBPF file. This is the format in Sims4 packages, worlds, etc"""
    _CONST_TYPE = 1
    _CONST_GROUP = 2
    _CONST_INSTAMCE_EX = 4
    
    def __init__(self, name):
        self.file = open(name, "rb")
        self._index_cache = None
        self._read_header()
        self._index_cache = list(self.scan_index())
    def _read_header(self):

        self.file.seek(0)
        buf = self.file.read(96)

        if buf[0:4] != b"DBPF":
            raise FormatException("Wrong magic")
        self.file_version = struct.unpack_from("=II", buf, 4)
        if self.file_version != (2,1):
            raise FormatException("Don't know how to handle anything other than DBPF v2.1")

        # TODO: check the accuracy of this; it's based on code I had
        # lying around for Sims3 DBPF files
        self._index_count, self._index_size, self._index_vsn, self._index_off = struct.unpack_from("=I4xI12xII", buf, 36)

    def _get_dword(self, dword=struct.Struct("=I")):
        """This is only ever intended to be called with no arguments; the
        dword kwarg is a function static"""
        return dword.unpack(self.file.read(4))[0]
    def scan_index(self, filter=None):
        if self._index_cache is not None:
            if filter is None:
                yield from self._index_cache
            else:
                yield from (_ for _ in self._index_cache
                              if filter.match(_.id))
            return
        if self._index_off == 0:
            raise FormatException("Missing index")
        self.file.seek(self._index_off)
        flags = self._get_dword()

        if flags & self._CONST_TYPE:
            entry_type = self._get_dword()
        if flags & self._CONST_GROUP:
            entry_group = self._get_dword()
        if flags & self._CONST_INSTAMCE_EX:
            entry_instance_ex = self._get_dword()
        
        for n in range(self._index_count):
            if not flags & self._CONST_TYPE:
                entry_type = self._get_dword()
            if not flags & self._CONST_GROUP:
                entry_group = self._get_dword()
            if not flags & self._CONST_INSTAMCE_EX:
                entry_instance_ex = self._get_dword()
            entry_instance = self._get_dword()
            entry_offset = self._get_dword()
            entry_size = self._get_dword()
            entry_size_decompressed = self._get_dword()
            if entry_size & 0x80000000:
                entry_compressed = struct.unpack("=HH", self.file.read(4))
            else:
                entry_compressed = (0,1)
            entry_size = entry_size & 0x7FFFFFFF
            rid = ResourceID(entry_group, (entry_instance_ex << 32) | entry_instance, entry_type)
            if filter is None or filter.match(rid):
                yield IndexEntry(rid, entry_offset, entry_size,
                                 entry_size_decompressed, entry_compressed)
            
    def __getitem__(self, item):
        if isinstance(item, int):
            item = self._index_cache[item]
        elif not isinstance(item, IndexEntry):
            # It must be a filter
            itemlist = self.scan_index(item)
            try:
                item = next(itemlist)
            except StopIteration:
                raise KeyError("No item found")
            try:
                next(itemlist)
            except StopIteration:
                pass
            else:
                raise KeyError("More than one item found")
        # At this point, we know that the item is an IndexEntry;
        # hopefully it is one that refers to this file ;-)
        self.file.seek(item.offset)
        ibuf = self.file.read(item.size)

        if item.compression[0] == 0:
            return ibuf # uncompressed
        elif item.compression[0] == 0xFFFE:
            # BUG: I'm guessing "streamable compression" is the same
            # as RefPack, with a limited buffer size. This may or may
            # not be true, and even if it is, I'd need to know the
            # size of the buffer to do anything sensible.
            return decodeRefPack(ibuf)
        elif item.compression[0] == 0xFFFF:
            return decodeRefPack(ibuf)
        elif item.deleted:
            raise KeyError("Deleted file")
        elif item.compression[0] == 0x5A42:
            # BUG: Not sure if the gzip header is needed. If it is,
            # change -15 in the next line to 15
            return zlib.decompress(ibuf, -15, item.size_decompressed) 

def decodeRefPack(ibuf):
    """Decode the DBPF compression. ibuf must quack like a bytes"""
    # Based on http://simswiki.info/wiki.php?title=Sims_3:DBPF/Compression
    # Sims4 compression has the first two bytes swapped
    
    iptr = optr = 0
    flags = ibuf[0]
    if ibuf[1] != 0xFB:
        raise FormatException("Invalid compressed data")
    iptr = 2
    osize = 0 # output size
    for _ in range(4 if flags & 0x80 else 3):
        osize = (osize << 8) | ibuf[iptr]
        iptr += 1

    obuf = bytearray(osize)
    while iptr < len(ibuf):
        numPlaintext = numToCopy = copyOffset = 0
        # Copyoffset is 0-indexed back from obuf[optr]
        # I.e., copyoffset=0 ==> copying starts at obuf[optr-1]
        
        # Read a control code
        cc0 = ibuf[iptr]; iptr+=1
        if cc0 <= 0x7F:
            cc1 = ibuf[iptr]; iptr+=1
            cc = (cc0,cc1)
            numPlaintext = cc0 & 0x03
            numToCopy = ((cc0 & 0x1C) >> 2) + 3
            copyOffset = ((cc0 & 0x60) << 3) + cc1
        elif cc0 <= 0xBF:
            cc1 = ibuf[iptr]; iptr+=1
            cc2 = ibuf[iptr]; iptr+=1
            cc = (cc0,cc1,cc2)
            numPlaintext = (cc1 & 0xC0) >> 6
            numToCopy = (cc0 & 0x3F) + 4
            copyOffset = ((cc1 & 0x3F) << 8) + cc2
        elif cc0 <= 0xDF:
            cc1 = ibuf[iptr]; iptr+=1
            cc2 = ibuf[iptr]; iptr+=1
            cc3 = ibuf[iptr]; iptr+=1
            cc = (cc0,cc1,cc2,cc3)
            numPlaintext = cc0 & 0x03
            numToCopy = ((cc0 & 0x0C) << 6) + cc3 + 5
            copyOffset = ((cc0 & 0x10) << 12) + (cc1 << 8) + cc2
        elif cc0 <= 0xFB:
            cc = (cc0,)
            numPlaintext = ((cc0 & 0x1F) << 2) + 4
            numToCopy = 0
        else:
            cc = (cc0,)
            numPlaintext = cc0 & 3
            numToCopy = 0

        # Copy from source
        obuf[optr:optr+numPlaintext] = ibuf[iptr:iptr+numPlaintext]
        iptr += numPlaintext
        optr += numPlaintext

        # Copy from output
        for _ in range(numToCopy):
            obuf[optr] = obuf[optr - 1 - copyOffset]
            optr += 1
    # Done decompressing
    return bytes(obuf)

        
