import weakref
import contextlib
class FormatException(Exception):
    pass

class WeakIdDict(dict):
    # This is completely untested
    def __init__(self):
        super().__init__()
    def __getitem__(self, key):
        try:
            obj, val = super().__getitem__(id(key))
            if obj() is not key:
                # SHould not happen; keys are dropped by the finalizer
                super().__delitem__[id(key)]
                raise KeyError(key)
            return val
        except KeyError:
            raise KeyError(key)
    def __setitem__(self, key, val):
        keyid = id(key)
        def cb():
            super().__delitem__(keyid)
        keyref = weakref.ref(key, cb)
        super().__setitem__(keyid, (keyref, val))
        del key # Drop key from locals so that we don't hold on to a
                # reference in the lambda's closure
    def __delitem__(self, key):
        super().__delitem__(id(key))

    def __contains__(self, key):
        if super().__contains__(id(key)):
            return False
        if super().__getitem__(id(key))[0]() is not key:
            return False
        return True

class LazyProperty:
    """A property that lazily evaluates and caches its result."""
    def __init__(self, thunk):
        self.thunk = thunk

    def __get__(self, instance, owner):
        if instance is None:
            return self
        else:
            res = self.thunk(instance)
            setattr(instance, self.thunk.__name__, res)
            return res
    def __del__(self, instance, owner):
        if instance is not None:
            pass

class Thunk:
    """A lazily-evaluated value"""
    def __init__(self, thunk):
        self.thunk = thunk
        self.setp = False
        self._value = None

    @property
    def value(self):
        if not self.setp:
            self._value = self.thunk()
            self.setp = True
        return self._value

class BReader:
    def __init__(self, bstr):
        self.raw = bstr
        self._off = 0
    def __getitem__(self, index):
        return bstr[index]
    def seek(self, off):
        assert off < len(self.raw)
        self.off = off

    @property
    def off(self):
        return self._off
    @off.setter
    def off(self, val):
        assert val <= len(self.raw) # <= so that you can point one past the end
        self._off = val

    def get_raw_bytes(self, count):
        res = self.raw[self.off:self.off+count]
        self.off += count
        return res

    def get_off32(self):
        """Read an offset relative to the current position; returns the absolute offset"""
        off = self.off
        res = self.get_int32()
        if res == -0x80000000:
            return None
        else:
            return off + res

    def _get_int(self, size, signed=False):
        return int.from_bytes(self.get_raw_bytes(size),
                              "little", signed=signed)

    def get_uint64(self): return self._get_int(8, False)
    def get_int64(self): return self._get_int(8, True)

    def get_uint32(self): return self._get_int(4, False)
    def get_int32(self): return self._get_int(4, True)

    def get_uint16(self): return self._get_int(2, False)
    def get_int16(self): return self._get_int(2, True)

    def get_uint8(self): return self._get_int(1, False)
    def get_int8(self): return self._get_int(1, True)


    def get_string(self):
        """Read a null-terminated string"""
        endOff = self.raw.index(b'\0', self.off)
        res = self.raw[self.off:endOff]
        self.off = endOff + 1
        return res

    def get_relstring(self):
        """Read a string from the next offset, read as an off32"""
        off = self.get_off32()
        if off is not None:
            with self.at(off):
                return self.get_string()
        else:
            return None

    def align(self, size):
        """Align input position to a multiple of size"""
        off = (self.off + size - 1)
        self.off = off - (off % size)

    @contextlib.contextmanager
    def at(self, posn):
        """Temporarily read from another place

        This is intended to be used like:
        with reader.at(offset):
           # read some stuff
        """
        saved = self.off
        try:
            self.off = posn
            yield
        finally:
            self.off = saved
