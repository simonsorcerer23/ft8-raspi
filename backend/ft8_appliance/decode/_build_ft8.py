"""CFFI build script for the ft8_lib bindings.

Run manually to (re)compile the native extension::

    python -m ft8_appliance.decode._build_ft8

The compiled module ends up at ``ft8_appliance/decode/_ft8_native*.so`` and is
loaded by :mod:`ft8_appliance.decode.ft8_native`.

This is the **spike** scope: we expose the bare minimum needed to prove the
toolchain works — message text encoding/decoding plus the symbol-level
``ft8_encode``. The full decode pipeline (monitor / waterfall / find_sync /
decode) lands in a later iteration.
"""

from __future__ import annotations

from pathlib import Path

from cffi import FFI

# Repo layout: <repo>/vendor/ft8_lib/  is the library checkout
REPO_ROOT = Path(__file__).resolve().parents[3]
FT8_LIB_DIR = REPO_ROOT / "vendor" / "ft8_lib"

ffi = FFI()

# Public C surface we want to call from Python.
ffi.cdef(
    """
    /* From ft8/message.h ------------------------------------------------ */

    typedef struct {
        uint8_t payload[10];
        uint16_t hash;
    } ftx_message_t;

    typedef enum {
        FTX_MESSAGE_RC_OK = 0,
        FTX_MESSAGE_RC_ERROR_CALLSIGN1,
        FTX_MESSAGE_RC_ERROR_CALLSIGN2,
        FTX_MESSAGE_RC_ERROR_SUFFIX,
        FTX_MESSAGE_RC_ERROR_GRID,
        FTX_MESSAGE_RC_ERROR_TYPE,
    } ftx_message_rc_t;

    typedef int ftx_field_t;    /* enum, fits in int */

    typedef struct {
        ftx_field_t types[3];   /* FTX_MAX_MESSAGE_FIELDS = 3 */
        int16_t offsets[3];
    } ftx_message_offsets_t;

    void ftx_message_init(ftx_message_t *msg);
    ftx_message_rc_t ftx_message_encode(
        ftx_message_t *msg,
        void *hash_if,           /* we pass NULL */
        const char *message_text);
    ftx_message_rc_t ftx_message_decode(
        const ftx_message_t *msg,
        void *hash_if,
        char *message_out,
        ftx_message_offsets_t *offsets);

    /* From ft8/encode.h ------------------------------------------------- */

    void ft8_encode(const uint8_t *payload, uint8_t *tones);
    void ft4_encode(const uint8_t *payload, uint8_t *tones);

    /* From our ft8_shim.c — the decode pipeline -------------------------- */

    typedef struct {
        char  message[40];
        int   snr_db_est;
        float dt_s;
        float freq_hz;
        int   score;
    } ft8_shim_result_t;

    int ft8_shim_decode_slot(
        const int16_t* pcm,
        int            n_samples,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_synth_message(
        const char* text,
        float       audio_freq_hz,
        float       amplitude,
        int16_t*    out_pcm,
        int         out_capacity
    );

    /* FT4 protocol — separate decode + synth functions in the shim.
     * Slot is 7.5 s (= 90000 samples @ 12 kHz), TX waveform is 60480
     * samples. Architecture §6.x FT4-Mode. */
    int ft4_shim_decode_slot(
        const int16_t* pcm,
        int            n_samples,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft4_shim_synth_message(
        const char* text,
        float       audio_freq_hz,
        float       amplitude,
        int16_t*    out_pcm,
        int         out_capacity
    );

    /* Sweep-B hooks: AP-decoding + Multi-Pass / Subtract.
     * Stubs in the shim today, real implementations later. */
    int ft8_shim_decode_slot_ap(
        const int16_t* pcm,
        int            n_samples,
        const char*    ap_callsigns,
        int            ap_callsigns_len,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_decode_slot_multipass(
        const int16_t* pcm,
        int            n_samples,
        int            num_passes,
        ft8_shim_result_t* out,
        int            max_out
    );

    /* v0.6.0 Anti-WSJT-X-Audit Phase B: tunable decoder.
     *   mode=0: standard (osr=2, LDPC=25)
     *   mode=1: deep     (osr=4, LDPC=50) — JTDX-Deep-Aequivalent
     *   mode=2: multi    Pass1 standard + Pass2 deep, dedupe */
    int ft8_shim_decode_slot_v2(
        const int16_t* pcm,
        int            n_samples,
        int            mode,
        ft8_shim_result_t* out,
        int            max_out
    );

    /* Callsign-Hash-Tabelle (v0.5.0): Python kann optional Calls
     * pre-populieren (z.B. aus DB worked-Calls) damit beim Boot die
     * <...>-Aufloesung sofort funktioniert. */
    int ft8_shim_hash_table_save(const char* callsign, uint32_t n22);
    int ft8_shim_hash_table_count(void);
    """
)

# Compile our shim alongside the existing libft8.a. The KISS-FFT objects
# are not bundled into libft8.a so we list them explicitly. We use a flat
# module name (no dots) so cffi puts the .so directly into ``out_dir``.
SHIM_C = str(Path(__file__).parent / "ft8_shim.c")
KISS_FFT_OBJS = [
    str(FT8_LIB_DIR / ".build" / "fft" / "kiss_fft.o"),
    str(FT8_LIB_DIR / ".build" / "fft" / "kiss_fftr.o"),
]

ffi.set_source(
    "_ft8_native",
    """
    #include "ft8/message.h"
    #include "ft8/encode.h"
    #include "ft8/decode.h"
    #include "common/monitor.h"

    typedef struct {
        char  message[40];
        int   snr_db_est;
        float dt_s;
        float freq_hz;
        int   score;
    } ft8_shim_result_t;

    int ft8_shim_decode_slot(
        const int16_t* pcm,
        int            n_samples,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_synth_message(
        const char* text,
        float       audio_freq_hz,
        float       amplitude,
        int16_t*    out_pcm,
        int         out_capacity
    );

    int ft4_shim_decode_slot(
        const int16_t* pcm,
        int            n_samples,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft4_shim_synth_message(
        const char* text,
        float       audio_freq_hz,
        float       amplitude,
        int16_t*    out_pcm,
        int         out_capacity
    );

    int ft8_shim_decode_slot_ap(
        const int16_t* pcm,
        int            n_samples,
        const char*    ap_callsigns,
        int            ap_callsigns_len,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_decode_slot_multipass(
        const int16_t* pcm,
        int            n_samples,
        int            num_passes,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_decode_slot_v2(
        const int16_t* pcm,
        int            n_samples,
        int            mode,
        ft8_shim_result_t* out,
        int            max_out
    );

    int ft8_shim_hash_table_save(const char* callsign, uint32_t n22);
    int ft8_shim_hash_table_count(void);
    """,
    sources=[SHIM_C],
    include_dirs=[str(FT8_LIB_DIR)],
    extra_objects=[
        str(FT8_LIB_DIR / "libft8.a"),
        *KISS_FFT_OBJS,
    ],
    libraries=["m"],
    # GCC 14 promotes -Wincompatible-pointer-types to an error by default.
    # The cdef declares ftx_field_t as `int` (the underlying enum type) but
    # the real header is an enum, which the compiler treats as a distinct
    # type. The values are still int-compatible at runtime — downgrade to
    # warning so the build proceeds.
    extra_compile_args=["-Wno-error=incompatible-pointer-types"],
)


if __name__ == "__main__":
    import os

    out_dir = Path(__file__).parent
    print(f"Compiling cffi extension into {out_dir}")
    os.chdir(out_dir)
    ffi.compile(tmpdir=".", verbose=True)
    print("done.")
