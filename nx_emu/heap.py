"""
A deliberately simple heap for the emulated process.

This is a single-shot "compile one shader, read the result, tear down"
program, not a long-running server, so a bump allocator that never truly
frees memory is fine -- it's far more robust than trying to reimplement a
real malloc, and there is a generous 256MB backing region. free()/glslc_Free
just record the block as released (for bookkeeping / double-free
detection); the space itself is not reclaimed.
"""


class GuestHeap:
    def __init__(self, base, size, align=16):
        self.base = base
        self.size = size
        self.align = align
        self._next = base
        self._end = base + size
        self._block_sizes = {}   # addr -> size, for realloc()/sizeof tracking
        self._freed = set()

    def alloc(self, nbytes, align=None):
        nbytes = max(1, nbytes)
        a = align if align else self.align
        a = max(1, a)
        addr = (self._next + a - 1) & ~(a - 1)
        if addr + nbytes > self._end:
            raise MemoryError(
                f"guest heap exhausted (wanted {nbytes} bytes, "
                f"{self._end - addr} left of {self.size} total)"
            )
        self._next = addr + nbytes
        self._block_sizes[addr] = nbytes
        self._freed.discard(addr)
        return addr

    def size_of(self, addr):
        return self._block_sizes.get(addr, 0)

    def free(self, addr):
        if addr == 0:
            return
        self._freed.add(addr)

    def is_valid(self, addr):
        return addr in self._block_sizes
