"""
Byte-for-byte layout of the structs in glslcinterface.h, for the AArch64
LP64 target ABI (8-byte pointers -- note every pointer field in the header
is wrapped in the GLSLC_PTR() union macro specifically so it's always 8
bytes wide regardless of host, which is why we don't need to worry about
32/64-bit ambiguity here).

Layout was derived by hand from the header (offsets computed using
standard AAPCS64 struct alignment: N-byte scalar aligned to N, struct
alignment = max member alignment, size rounded up to alignment) and cross
-checked against the natural sizes implied by each struct's own
`reserved[]` padding, which line up cleanly -- a good sign the packing
below matches what the real compiler used to build glslc.elf.

GLSLCoptionFlags is the one tricky part: it's 19 bitfields mixing
`uint32_t` and 4-byte-enum storage. Simulating GCC/Clang's "don't split a
field across a storage-unit boundary" packing rule, the fields divide
*exactly* into two 32-bit words (32 bits then 7 bits) with zero slack --
strong evidence this is the intended packing (and it's why we hand-roll
the bit packing instead of trusting ctypes.Structure bitfields, whose
layout algorithm differs between MSVC and GCC and is NOT guaranteed to
match an ARM GCC/Clang-compiled ABI just because you're running Python
locally).
"""
import struct

# ---------------------------------------------------------------- enums ---
NVN_SHADER_STAGE_VERTEX = 0
NVN_SHADER_STAGE_FRAGMENT = 1
NVN_SHADER_STAGE_GEOMETRY = 2
NVN_SHADER_STAGE_TESS_CONTROL = 3
NVN_SHADER_STAGE_TESS_EVALUATION = 4
NVN_SHADER_STAGE_COMPUTE = 5

GLSLC_LANGUAGE_GLSL = 0
GLSLC_LANGUAGE_GLES = 1
GLSLC_LANGUAGE_SPIRV = 2

GLSLC_DEBUG_LEVEL_NONE = 0
GLSLC_DEBUG_LEVEL_G0 = 1
GLSLC_DEBUG_LEVEL_G1 = 2
GLSLC_DEBUG_LEVEL_G2 = 3

DEFAULT_SPILL = 0
NO_SPILL = 1

GLSLC_OPTLEVEL_DEFAULT = 0
GLSLC_OPTLEVEL_NONE = 1

GLSLC_LOOP_UNROLL_DEFAULT = 0
GLSLC_LOOP_UNROLL_NONE = 1
GLSLC_LOOP_UNROLL_ALL = 2

GLSLC_WARN_UNINIT_DEFAULT = 0
GLSLC_WARN_UNINIT_NONE = 1
GLSLC_WARN_UNINIT_ALL = 2

GLSLC_INIT_ERROR_UNINITIALIZED = 0
GLSLC_INIT_SUCCESS = 1
GLSLC_INIT_ERROR_ALLOC_FAILURE = 2
GLSLC_INIT_ERROR_NO_ALLOC_CALLBACKS_SET = 3


# ------------------------------------------------------- GLSLCoptionFlags -
def pack_option_flags(
    glslSeparable=0, outputAssembly=0, outputGpuBinaries=0, outputPerfStats=0,
    outputShaderReflection=0, language=GLSLC_LANGUAGE_GLSL,
    outputDebugInfo=GLSLC_DEBUG_LEVEL_NONE, spillControl=DEFAULT_SPILL,
    outputThinGpuBinaries=0, tessellationAndPassthroughGS=0,
    prioritizeConsecutiveTextureInstructions=0, enableFastMathMask=0,
    optLevel=GLSLC_OPTLEVEL_DEFAULT, unrollControl=GLSLC_LOOP_UNROLL_DEFAULT,
    errorOnScratchMemUsage=0, enableCBFOptimization=0, enableWarpCulling=0,
    enableMultithreadCompilation=0, warnUninitControl=GLSLC_WARN_UNINIT_DEFAULT,
):
    word0 = 0
    word0 |= (glslSeparable & 1) << 0
    word0 |= (outputAssembly & 1) << 1
    word0 |= (outputGpuBinaries & 1) << 2
    word0 |= (outputPerfStats & 1) << 3
    word0 |= (outputShaderReflection & 1) << 4
    word0 |= (language & 0xF) << 5
    word0 |= (outputDebugInfo & 0xF) << 9
    word0 |= (spillControl & 0xF) << 13
    word0 |= (outputThinGpuBinaries & 1) << 17
    word0 |= (tessellationAndPassthroughGS & 1) << 18
    word0 |= (prioritizeConsecutiveTextureInstructions & 1) << 19
    word0 |= (enableFastMathMask & 0x3F) << 20
    word0 |= (optLevel & 0x7) << 26
    word0 |= (unrollControl & 0x7) << 29

    word1 = 0
    word1 |= (errorOnScratchMemUsage & 1) << 0
    word1 |= (enableCBFOptimization & 1) << 1
    word1 |= (enableWarpCulling & 1) << 2
    word1 |= (enableMultithreadCompilation & 1) << 3
    word1 |= (warnUninitControl & 0x7) << 4

    return struct.pack('<II', word0, word1)


def unpack_option_flags(data):
    word0, word1 = struct.unpack('<II', data[:8])
    return {
        'glslSeparable': word0 & 1,
        'outputAssembly': (word0 >> 1) & 1,
        'outputGpuBinaries': (word0 >> 2) & 1,
        'outputPerfStats': (word0 >> 3) & 1,
        'outputShaderReflection': (word0 >> 4) & 1,
        'language': (word0 >> 5) & 0xF,
        'outputDebugInfo': (word0 >> 9) & 0xF,
        'spillControl': (word0 >> 13) & 0xF,
        'outputThinGpuBinaries': (word0 >> 17) & 1,
        'tessellationAndPassthroughGS': (word0 >> 18) & 1,
        'prioritizeConsecutiveTextureInstructions': (word0 >> 19) & 1,
        'enableFastMathMask': (word0 >> 20) & 0x3F,
        'optLevel': (word0 >> 26) & 0x7,
        'unrollControl': (word0 >> 29) & 0x7,
        'errorOnScratchMemUsage': word1 & 1,
        'enableCBFOptimization': (word1 >> 1) & 1,
        'enableWarpCulling': (word1 >> 2) & 1,
        'enableMultithreadCompilation': (word1 >> 3) & 1,
        'warnUninitControl': (word1 >> 4) & 0x7,
    }


OPTION_FLAGS_SIZE = 8

# --------------------------------------------------------- GLSLCincludeInfo
# GLSLC_PTR(paths) u64 @0 ; numPaths u32 @8 ; reserved[32] @12  -> size 48
INCLUDE_INFO_SIZE = 48
INCLUDE_INFO_OFF_PATHS = 0
INCLUDE_INFO_OFF_NUM_PATHS = 8

# ------------------------------------------------------------- GLSLCxfbInfo
# GLSLC_PTR(varyings) u64 @0 ; numVaryings u32 @8 ; reserved[32] @12 -> 48
XFB_INFO_SIZE = 48
XFB_INFO_OFF_VARYINGS = 0
XFB_INFO_OFF_NUM_VARYINGS = 8

# ------------------------------------------------------------- GLSLCoptions
# forceIncludeStdHeader u64 @0
# optionFlags            8  @8
# includeInfo            48 @16
# xfbVaryingInfo         48 @64
# reserved[32]              @112
# size = 144
OPTIONS_SIZE = 144
OPTIONS_OFF_FORCE_INCLUDE_STD_HEADER = 0
OPTIONS_OFF_OPTION_FLAGS = 8
OPTIONS_OFF_INCLUDE_INFO = 16
OPTIONS_OFF_XFB_VARYING_INFO = 64

# --------------------------------------------------------------- GLSLCinput
# sources u64 @0 ; stages u64 @8 ; count u8 @16 ; reserved[7] @17
# spirvEntryPointNames u64 @24 ; spirvModuleSizes u64 @32 ; spirvSpecInfo u64 @40
# size = 48
INPUT_SIZE = 48
INPUT_OFF_SOURCES = 0
INPUT_OFF_STAGES = 8
INPUT_OFF_COUNT = 16
INPUT_OFF_SPIRV_ENTRY_POINT_NAMES = 24
INPUT_OFF_SPIRV_MODULE_SIZES = 32
INPUT_OFF_SPIRV_SPEC_INFO = 40

# ------------------------------------------------------ GLSLCcompileObject
# lastCompiledResults u64 @0
# reflectionSection    u64 @8
# privateData          u64 @16
# options       (144)      @24
# input          (48)      @168
# initStatus     (4, enum) @216
# reserved[28]              @220
# size = 248
COMPILE_OBJECT_SIZE = 248
CO_OFF_LAST_COMPILED_RESULTS = 0
CO_OFF_REFLECTION_SECTION = 8
CO_OFF_PRIVATE_DATA = 16
CO_OFF_OPTIONS = 24
CO_OFF_INPUT = 24 + 144  # 168
CO_OFF_INIT_STATUS = 168 + 48  # 216

# ------------------------------------------------------------- GLSLCresults
# compilationStatus u64 @0 ; glslcOutput u64 @8 ; reserved[32] @16 -> 48
RESULTS_SIZE = 48
RESULTS_OFF_COMPILATION_STATUS = 0
RESULTS_OFF_GLSLC_OUTPUT = 8

# ----------------------------------------------------- GLSLCcompilationStatus
# infoLog u64 @0 ; infoLogLength u32 @8 ; success u8 @12 ; allocError u8 @13
# usedMTSpecialization u8 @14 ; reserved[1] @15 ; numEntriesInBatch u32 @16
# reserved2[24] @20  -> raw 44, padded to 48 (8-byte align from the pointer)
COMPILATION_STATUS_SIZE = 48
CS_OFF_INFO_LOG = 0
CS_OFF_INFO_LOG_LENGTH = 8
CS_OFF_SUCCESS = 12
CS_OFF_ALLOC_ERROR = 13

# --------------------------------------------------------------- GLSLCoutput
# magic u32@0 reservedBits u32@4 optionFlags(8)@8 versionInfo(52)@16
# size u32@68 dataOffset u32@72 numSections u32@76 reserved[64]@80 headers@144
OUTPUT_OFF_MAGIC = 0
OUTPUT_OFF_SIZE = 68
OUTPUT_OFF_DATA_OFFSET = 72
OUTPUT_OFF_NUM_SECTIONS = 76


def _build_ptr_array(emu, ptrs):
    """Write a list of already-resident guest addresses as a contiguous
    uint64_t[] in guest memory (i.e. a `T* const*`-style array) and return
    its address. Returns 0 for an empty/None list (a NULL array pointer)."""
    if not ptrs:
        return 0
    arr = emu.heap.alloc(8 * len(ptrs))
    emu.write_bytes(arr, struct.pack(f'<{len(ptrs)}Q', *ptrs))
    return arr


def _build_cstr_array(emu, strings):
    """Write each Python str as a NUL-terminated C string, then bundle
    their addresses into a `const char* const*` array. Returns 0 for an
    empty/None list."""
    if not strings:
        return 0
    ptrs = [emu.write_cstr(s) for s in strings]
    return _build_ptr_array(emu, ptrs)


def new_compile_object(emu, options_bytes, input_bytes):
    """Allocate + zero a GLSLCcompileObject, then splice in the pre-built
    `options` (144 bytes) and `input` (48 bytes) sub-structs."""
    assert len(options_bytes) == OPTIONS_SIZE
    assert len(input_bytes) == INPUT_SIZE
    addr = emu.heap.alloc(COMPILE_OBJECT_SIZE)
    emu.write_bytes(addr, b'\x00' * COMPILE_OBJECT_SIZE)
    emu.write_bytes(addr + CO_OFF_OPTIONS, options_bytes)
    emu.write_bytes(addr + CO_OFF_INPUT, input_bytes)
    return addr


def build_options(
    force_include_std_header=None,
    glslSeparable=False, outputAssembly=False, outputGpuBinaries=False,
    outputPerfStats=False, outputShaderReflection=False,
    language=GLSLC_LANGUAGE_GLSL, outputDebugInfo=GLSLC_DEBUG_LEVEL_NONE,
    spillControl=DEFAULT_SPILL, outputThinGpuBinaries=False,
    tessellationAndPassthroughGS=False,
    prioritizeConsecutiveTextureInstructions=False, enableFastMathMask=0,
    optLevel=GLSLC_OPTLEVEL_DEFAULT, unrollControl=GLSLC_LOOP_UNROLL_DEFAULT,
    errorOnScratchMemUsage=False, enableCBFOptimization=False,
    enableWarpCulling=False, enableMultithreadCompilation=False,
    warnUninitControl=GLSLC_WARN_UNINIT_DEFAULT,
    include_paths=None, xfb_varyings=None,
    emu=None,
):
    """Returns 144 raw bytes for a GLSLCoptions struct -- every field of
    GLSLCoptions that's meant to be filled in by the caller (as opposed to
    the `reserved[]` padding) is a parameter here:

      forceIncludeStdHeader -> force_include_std_header: str
      optionFlags.*         -> the GLSLCoptionFlags-bit kwargs (glslSeparable..warnUninitControl)
      includeInfo            -> include_paths: list[str]  (#include search paths)
      xfbVaryingInfo          -> xfb_varyings: list[str]   (transform-feedback varying names)

    `emu` must be passed whenever force_include_std_header, include_paths,
    or xfb_varyings is set, since those need to write into guest memory.
    """
    buf = bytearray(OPTIONS_SIZE)
    if force_include_std_header:
        assert emu is not None, "pass emu= if you set force_include_std_header"
        ptr = emu.write_cstr(force_include_std_header)
        struct.pack_into('<Q', buf, OPTIONS_OFF_FORCE_INCLUDE_STD_HEADER, ptr)

    flags = pack_option_flags(
        glslSeparable=int(glslSeparable), outputAssembly=int(outputAssembly),
        outputGpuBinaries=int(outputGpuBinaries), outputPerfStats=int(outputPerfStats),
        outputShaderReflection=int(outputShaderReflection), language=language,
        outputDebugInfo=outputDebugInfo, spillControl=spillControl,
        outputThinGpuBinaries=int(outputThinGpuBinaries),
        tessellationAndPassthroughGS=int(tessellationAndPassthroughGS),
        prioritizeConsecutiveTextureInstructions=int(prioritizeConsecutiveTextureInstructions),
        enableFastMathMask=enableFastMathMask, optLevel=optLevel, unrollControl=unrollControl,
        errorOnScratchMemUsage=int(errorOnScratchMemUsage),
        enableCBFOptimization=int(enableCBFOptimization),
        enableWarpCulling=int(enableWarpCulling),
        enableMultithreadCompilation=int(enableMultithreadCompilation),
        warnUninitControl=warnUninitControl,
    )
    buf[OPTIONS_OFF_OPTION_FLAGS:OPTIONS_OFF_OPTION_FLAGS + 8] = flags

    if include_paths:
        assert emu is not None, "pass emu= if you set include_paths"
        arr = _build_cstr_array(emu, include_paths)
        struct.pack_into('<Q', buf, OPTIONS_OFF_INCLUDE_INFO + INCLUDE_INFO_OFF_PATHS, arr)
        struct.pack_into('<I', buf, OPTIONS_OFF_INCLUDE_INFO + INCLUDE_INFO_OFF_NUM_PATHS, len(include_paths))

    if xfb_varyings:
        assert emu is not None, "pass emu= if you set xfb_varyings"
        arr = _build_cstr_array(emu, xfb_varyings)
        struct.pack_into('<Q', buf, OPTIONS_OFF_XFB_VARYING_INFO + XFB_INFO_OFF_VARYINGS, arr)
        struct.pack_into('<I', buf, OPTIONS_OFF_XFB_VARYING_INFO + XFB_INFO_OFF_NUM_VARYINGS, len(xfb_varyings))

    return bytes(buf)


def build_input(
    emu, sources, stages,
    spirv_entry_point_names=None, spirv_module_sizes=None, spirv_spec_info=None,
):
    """Returns 48 raw bytes for a GLSLCinput struct -- every field of
    GLSLCinput that's meant to be filled in by the caller (as opposed to
    the `reserved[]` padding) is a parameter here:

      sources -> sources: list[str | bytes]  (one entry per shader)
      stages  -> stages:  list[int]           NVN_SHADER_STAGE_* (same length as sources)
      count   -> set automatically from len(sources)

    For GLSL/GLES text sources, pass a Python str per entry -- it's written
    as a NUL-terminated C string, matching `const char* const* sources`.

    For SPIR-V input (GLSLC_LANGUAGE_SPIRV in options), sources can't rely
    on NUL-termination since the binary can contain embedded zero words, so
    pass `bytes` per entry instead -- it's written verbatim -- and use the
    two spirv_* parameters below (both required together, one entry per
    source, same length as sources):

      spirvEntryPointNames -> spirv_entry_point_names: list[str]   (e.g. "main" per module)
      spirvModuleSizes     -> spirv_module_sizes:      list[int]   (byte length per module)

    spirvSpecInfo -> spirv_spec_info: list[bytes | None]
      NOTE: glslcinterface.h itself marks GLSLCspirvSpecializationInfo's
      layout as "todo: reverse from nnSdk, type never used" -- NVIDIA never
      shipped a definition of what's actually inside it, so this library
      can't build one for you. If you've reversed the real layout yourself,
      pass one pre-built raw blob per source (or None for "no
      specialization" on that particular module) and they'll be written to
      guest memory and pointed at correctly; otherwise just leave this
      alone -- it's zeroed (i.e. no specialization info) by default, which
      is the correct value for ordinary GLSL/GLES compiles.
    """
    assert len(sources) == len(stages)
    # 0x105fc41x's own count check (`cmp w8, #7; b.hs <reject>` on this
    # exact byte) rejects count >= 7 at runtime, so 6 is the real ceiling,
    # not just a made-up sanity limit.
    assert 0 < len(sources) <= 6, "glslc.elf itself rejects an input.count >= 7"
    n = len(sources)

    src_ptrs = [emu.write_blob(s) if isinstance(s, (bytes, bytearray)) else emu.write_cstr(s)
                for s in sources]
    sources_arr = _build_ptr_array(emu, src_ptrs)
    stages_arr = emu.heap.alloc(4 * n)
    emu.write_bytes(stages_arr, struct.pack(f'<{n}I', *stages))

    buf = bytearray(INPUT_SIZE)
    struct.pack_into('<Q', buf, INPUT_OFF_SOURCES, sources_arr)
    struct.pack_into('<Q', buf, INPUT_OFF_STAGES, stages_arr)
    buf[INPUT_OFF_COUNT] = n

    if spirv_entry_point_names:
        assert len(spirv_entry_point_names) == n
        arr = _build_cstr_array(emu, spirv_entry_point_names)
        struct.pack_into('<Q', buf, INPUT_OFF_SPIRV_ENTRY_POINT_NAMES, arr)

    if spirv_module_sizes:
        assert len(spirv_module_sizes) == n
        arr = emu.heap.alloc(4 * n)
        emu.write_bytes(arr, struct.pack(f'<{n}I', *spirv_module_sizes))
        struct.pack_into('<Q', buf, INPUT_OFF_SPIRV_MODULE_SIZES, arr)

    if spirv_spec_info:
        assert len(spirv_spec_info) == n
        ptrs = [emu.write_blob(blob) if blob is not None else 0 for blob in spirv_spec_info]
        arr = _build_ptr_array(emu, ptrs)
        struct.pack_into('<Q', buf, INPUT_OFF_SPIRV_SPEC_INFO, arr)

    return bytes(buf)


def read_compile_results(emu, compile_object_addr):
    """After glslcCompile(), pull out (success, info_log, output_blob_or_None, full_blob_or_None).

    GLSLCoutput's `size`/`dataOffset` work exactly like the `size`/`dataOffset`
    pair inside GLSLCsectionHeaderCommon (same field order, same meaning, just
    one level up): `dataOffset` is the byte offset -- from the start of the
    GLSLCoutput struct -- where the actual compiled data begins, and `size` is
    the length of that data. Everything before `dataOffset` is just the fixed
    header fields plus the `headers[numSections]` section-descriptor table --
    metadata, not shader bytes.

    So:
      - `output_blob` is the real payload: raw[dataOffset : dataOffset+size].
        This is what you want if all you need is the compiled shader data
        itself (the common case -- one GPU code section).
      - `full_blob` is the *complete* GLSLCoutput struct (headers included),
        i.e. raw[0 : dataOffset+size]. Keep this around if you enabled more
        than one output section (e.g. --output-shader-reflection alongside
        the default GPU binary) -- in that case `output_blob` is *all* of
        those sections' data concatenated together with no way to tell them
        apart on its own, and you need to walk full_blob's
        `headers[i].genericHeader.common.{dataOffset,size,type}` (offsets
        relative to the start of full_blob, per Nintendo's own GLSLC
        programming guide) to pull out each section individually.
    """
    results_ptr = emu.read_u64(compile_object_addr + CO_OFF_LAST_COMPILED_RESULTS)
    if results_ptr == 0:
        return False, "(no GLSLCresults produced)", None, None

    status_ptr = emu.read_u64(results_ptr + RESULTS_OFF_COMPILATION_STATUS)
    output_ptr = emu.read_u64(results_ptr + RESULTS_OFF_GLSLC_OUTPUT)

    success = False
    info_log = ""
    if status_ptr:
        success = emu.read_u8(status_ptr + CS_OFF_SUCCESS) != 0
        log_ptr = emu.read_u64(status_ptr + CS_OFF_INFO_LOG)
        log_len = emu.read_u32(status_ptr + CS_OFF_INFO_LOG_LENGTH)
        if log_ptr:
            raw = emu.read_bytes(log_ptr, log_len) if log_len else emu.read_cstr(log_ptr)
            info_log = raw.decode('utf-8', errors='replace').rstrip('\x00')

    output_blob = None
    full_blob = None
    if output_ptr:
        data_offset = emu.read_u32(output_ptr + OUTPUT_OFF_DATA_OFFSET)
        data_size = emu.read_u32(output_ptr + OUTPUT_OFF_SIZE)
        total_len = data_offset + data_size
        if 0 < data_size and 0 < total_len < (1 << 28):
            full_blob = emu.read_bytes(output_ptr, total_len)
            output_blob = full_blob[data_offset:]

    return success, info_log, output_blob, full_blob
