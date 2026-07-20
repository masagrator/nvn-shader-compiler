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
OUTPUT_OFF_HEADERS = 144

# --------------------------------------------------- GLSLCsectionHeaderUnion
# Every section header starts with the 44-byte GLSLCsectionHeaderCommon
# (size u32@0, dataOffset u32@4, type enum(i32)@8, reserved[32]@12), and the
# array entry stride is sizeof(GLSLCsectionHeaderUnion) = 144 (the largest
# member, GLSLCgpuCodeHeader).
#
# IMPORTANT, and the actual point of this comment block: `common.dataOffset`
# is an ABSOLUTE byte offset from the start of the whole GLSLCoutput blob
# (not relative to GLSLCoutput.dataOffset, even though for whichever
# section happens to be laid out first the two coincide) and `common.size`
# is that section's exact, unpadded payload length. Sections are packed
# back-to-back, each one 8-byte aligned, i.e.
# sections[i+1].dataOffset == round_up_8(sections[i].dataOffset + sections[i].size).
# Verified directly against a real compiled blob: with 4 sections the
# offsets/sizes chain together exactly (1008+2472->3480, 3480+264->3744,
# 3744+268->4016 after 8-byte rounding, 4016+462->4480 == the blob's total
# `size`, again after 8-byte rounding).
SECTION_HEADER_STRIDE = 144
SECTION_COMMON_SIZE = 44
SEC_OFF_SIZE = 0
SEC_OFF_DATA_OFFSET = 4
SEC_OFF_TYPE = 8

GLSLC_SECTION_TYPE_GPU_CODE = 0
GLSLC_SECTION_TYPE_ASM_DUMP = 1
GLSLC_SECTION_TYPE_PERF_STATS = 2
GLSLC_SECTION_TYPE_REFLECTION = 3
GLSLC_SECTION_TYPE_DEBUG_INFO = 4
SECTION_TYPE_NAMES = {
    GLSLC_SECTION_TYPE_GPU_CODE: 'GPU_CODE',
    GLSLC_SECTION_TYPE_ASM_DUMP: 'ASM_DUMP',
    GLSLC_SECTION_TYPE_PERF_STATS: 'PERF_STATS',
    GLSLC_SECTION_TYPE_REFLECTION: 'REFLECTION',
    GLSLC_SECTION_TYPE_DEBUG_INFO: 'DEBUG_INFO',
}
STAGE_NAMES_BY_VALUE = {
    NVN_SHADER_STAGE_VERTEX: 'vertex', NVN_SHADER_STAGE_FRAGMENT: 'fragment',
    NVN_SHADER_STAGE_GEOMETRY: 'geometry', NVN_SHADER_STAGE_TESS_CONTROL: 'tess_control',
    NVN_SHADER_STAGE_TESS_EVALUATION: 'tess_evaluation', NVN_SHADER_STAGE_COMPUTE: 'compute',
}

# GLSLCgpuCodeHeader fields beyond `common` (offsets from the section
# entry's own start, i.e. absolute-within-entry, not relative to `common`).
# controlOffset/dataOffset here are themselves relative to THIS section's
# own data region (whose absolute start in the blob is common.dataOffset) --
# confirmed empirically: controlOffset=0, dataOffset=controlSize (control
# then code, back to back), and controlSize+dataSize == common.size exactly.
GPU_CODE_OFF_STAGE = 44
GPU_CODE_OFF_CONTROL_OFFSET = 48
GPU_CODE_OFF_DATA_OFFSET = 52
GPU_CODE_OFF_DATA_SIZE = 56
GPU_CODE_OFF_CONTROL_SIZE = 60
GPU_CODE_OFF_SCRATCH_PER_WARP = 64
GPU_CODE_OFF_SCRATCH_RECOMMENDED = 68

# GLSLCasmDumpHeader field beyond `common`
ASM_DUMP_OFF_STAGE = 44


def parse_glslc_output(blob):
    """Parse a raw GLSLCoutput blob (as returned by read_compile_results)
    into a structured dict, separating the actual shader payload out from
    the container/header bytes.

    Returns:
      {
        'magic': int, 'size': int, 'dataOffset': int, 'numSections': int,
        'sections': [
          {
            'type': int, 'type_name': str,
            'size': int,          # this section's exact payload length
            'data_offset': int,   # absolute offset of this section's payload within the blob
            'data': bytes,        # blob[data_offset : data_offset+size] -- this section's raw payload
            # present only for type == GLSLC_SECTION_TYPE_GPU_CODE:
            'stage': int, 'stage_name': str,
            'control': bytes,     # the NVN "control" segment -- required alongside code
            'code': bytes,        # the actual GPU machine code -- this is "the shader"
          }, ...
        ],
      }
    """
    magic = struct.unpack_from('<I', blob, OUTPUT_OFF_MAGIC)[0]
    size = struct.unpack_from('<I', blob, OUTPUT_OFF_SIZE)[0]
    data_offset = struct.unpack_from('<I', blob, OUTPUT_OFF_DATA_OFFSET)[0]
    num_sections = struct.unpack_from('<I', blob, OUTPUT_OFF_NUM_SECTIONS)[0]

    sections = []
    for i in range(num_sections):
        base = OUTPUT_OFF_HEADERS + i * SECTION_HEADER_STRIDE
        sec_size = struct.unpack_from('<I', blob, base + SEC_OFF_SIZE)[0]
        sec_data_offset = struct.unpack_from('<I', blob, base + SEC_OFF_DATA_OFFSET)[0]
        sec_type = struct.unpack_from('<i', blob, base + SEC_OFF_TYPE)[0]
        entry = {
            'type': sec_type,
            'type_name': SECTION_TYPE_NAMES.get(sec_type, f'UNKNOWN({sec_type})'),
            'size': sec_size,
            'data_offset': sec_data_offset,
            'data': bytes(blob[sec_data_offset:sec_data_offset + sec_size]),
        }
        if sec_type == GLSLC_SECTION_TYPE_GPU_CODE:
            stage = struct.unpack_from('<I', blob, base + GPU_CODE_OFF_STAGE)[0]
            control_off = struct.unpack_from('<I', blob, base + GPU_CODE_OFF_CONTROL_OFFSET)[0]
            code_off = struct.unpack_from('<I', blob, base + GPU_CODE_OFF_DATA_OFFSET)[0]
            code_size = struct.unpack_from('<I', blob, base + GPU_CODE_OFF_DATA_SIZE)[0]
            control_size = struct.unpack_from('<I', blob, base + GPU_CODE_OFF_CONTROL_SIZE)[0]
            entry['stage'] = stage
            entry['stage_name'] = STAGE_NAMES_BY_VALUE.get(stage, f'stage{stage}')
            abs_control = sec_data_offset + control_off
            abs_code = sec_data_offset + code_off
            entry['control'] = bytes(blob[abs_control:abs_control + control_size])
            entry['code'] = bytes(blob[abs_code:abs_code + code_size])
        elif sec_type == GLSLC_SECTION_TYPE_ASM_DUMP:
            entry['stage'] = struct.unpack_from('<I', blob, base + ASM_DUMP_OFF_STAGE)[0]
            entry['stage_name'] = STAGE_NAMES_BY_VALUE.get(entry['stage'], f"stage{entry['stage']}")
        sections.append(entry)

    return {'magic': magic, 'size': size, 'dataOffset': data_offset,
            'numSections': num_sections, 'sections': sections}


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
    """After glslcCompile(), pull out (success, info_log, output_blob_or_None)."""
    results_ptr = emu.read_u64(compile_object_addr + CO_OFF_LAST_COMPILED_RESULTS)
    if results_ptr == 0:
        return False, "(no GLSLCresults produced)", None

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
    if output_ptr:
        blob_size = emu.read_u32(output_ptr + OUTPUT_OFF_SIZE)
        if 0 < blob_size < (1 << 28):
            output_blob = emu.read_bytes(output_ptr, blob_size)

    return success, info_log, output_blob
