/* ft8_shim.c — thin C wrapper exposing the ft8_lib decode pipeline as a
 * single function callable from Python via cffi.
 *
 * Mirrors the decode flow from vendor/ft8_lib/demo/decode_ft8.c:
 *   monitor_init -> for each block: monitor_process -> ftx_find_candidates
 *   -> for each candidate: ftx_decode_candidate -> ftx_message_decode
 *
 * Designed to be re-entrant per slot: no static state besides function-
 * local arrays. Hash-based duplicate detection uses message.hash; we
 * keep a small fixed-size set local to the call. Non-standard
 * callsign hash lookups (the "<XYZ>" syntax) are not supported here —
 * the callsign hash interface is passed as NULL.
 */

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#include "ft8/decode.h"
#include "ft8/message.h"
#include "common/monitor.h"
#include "ft8/ldpc.h"
#include "ft8/crc.h"
#include "ft8/constants.h"

/* ===================================================================
 * Callsign Hash-Tabelle (Sebastian-Request 2026-05-24, v0.5.0)
 * ===================================================================
 *
 * FT8 hashed compound/lange Calls auf 22 Bits (mit Sub-Hashes 12/10
 * Bits). Decoder zeigen sie als "<...>" wenn der Empfaenger den Hash
 * nicht aufloesen kann. ft8_lib bietet ein Hash-Interface mit
 * lookup_hash + save_hash Callbacks — wir wireup'en das mit einer
 * static circular-buffer-Tabelle und reichen den Interface-Pointer
 * an ftx_message_decode weiter. Dann macht ft8_lib alles:
 *   - save_hash() wird automatisch aufgerufen wenn ein vollstaendiger
 *     Compound-Call decoded wird (z.B. "EK/RX3DPK" als Sender in
 *     einer Standard-Message)
 *   - lookup_hash() wird beim Unhashen aufgerufen wenn der Decoder
 *     auf einen Hash-Slot trifft (z.B. "<HASH22>")
 * Ergebnis: spaeter empfangene "<...>"-Messages werden zu vollen Calls
 * aufgeloest, sofern wir den Call vorher mal mit-vollem-Namen
 * gesehen haben.
 *
 * KEINE Synchronisation — decode_slot() laeuft sequenziell (Python-
 * Pool-Worker single-threaded fuer diese Funktion). Bei zukuenftiger
 * Parallelisierung muesste das ein mutex bekommen.
 */
#define HASH_TABLE_SIZE 1024  /* 2026-09-06: war 256; Hint-Pass validiert gegen bekannte Calls, mehr Kandidaten = mehr Treffer */

typedef struct {
    uint32_t n22;
    char     call[14];   /* max FT8 callsign length 11 chars + null + pad */
    bool     used;
} hash_entry_t;

static hash_entry_t s_hash_table[HASH_TABLE_SIZE];
static int s_hash_head = 0;  /* circular buffer write index */

static bool shim_lookup_hash(ftx_callsign_hash_type_t type, uint32_t hash, char* call_out) {
    for (int i = 0; i < HASH_TABLE_SIZE; ++i) {
        if (!s_hash_table[i].used) continue;
        uint32_t stored = s_hash_table[i].n22;
        bool match = false;
        switch (type) {
            case FTX_CALLSIGN_HASH_22_BITS:
                match = (stored == hash);
                break;
            case FTX_CALLSIGN_HASH_12_BITS:
                /* n12 = n22 >> 10 (siehe ft8_lib message.c::save_callsign) */
                match = ((stored >> 10) == hash);
                break;
            case FTX_CALLSIGN_HASH_10_BITS:
                /* n10 = n22 >> 12 */
                match = ((stored >> 12) == hash);
                break;
        }
        if (match) {
            /* 2026-09-06: ft8_lib reicht hier ein char[12] durch
             * (message.c lookup_callsign: c11). 13+NUL hat den Stack
             * zerlegt — "stack smashing detected", Controller tot —
             * sobald ein Hash-Call mit Tabellentreffer decodiert wurde.
             * Calls sind ohnehin max. 11 Zeichen (pack58). */
            strncpy(call_out, s_hash_table[i].call, 11);
            call_out[11] = '\0';
            return true;
        }
    }
    return false;
}

static void shim_save_hash(const char* callsign, uint32_t n22) {
    /* Dedup: existing entry mit gleichem n22 -> Call aktualisieren
     * (sollte gleich sein, aber defensiv). */
    for (int i = 0; i < HASH_TABLE_SIZE; ++i) {
        if (s_hash_table[i].used && s_hash_table[i].n22 == n22) {
            strncpy(s_hash_table[i].call, callsign, 13);
            s_hash_table[i].call[13] = '\0';
            return;
        }
    }
    /* Neuer Eintrag in head-Slot (overwrite oldest). */
    s_hash_table[s_hash_head].n22 = n22;
    strncpy(s_hash_table[s_hash_head].call, callsign, 13);
    s_hash_table[s_hash_head].call[13] = '\0';
    s_hash_table[s_hash_head].used = true;
    s_hash_head = (s_hash_head + 1) % HASH_TABLE_SIZE;
}

static void shim_save_hash_noop(const char* callsign, uint32_t n22) { (void)callsign; (void)n22; }

static ftx_callsign_hash_interface_t s_hash_if = {
    .lookup_hash = shim_lookup_hash,
    .save_hash   = shim_save_hash,
};

/* 2026-09-06: Lese-Interface fuer das Known-Call-Gate. ftx_message_decode
 * speichert sonst die gerade entpackten Calls in unserer Tabelle — und das
 * Gate fand danach "bekannt", was es selbst eben eingetragen hatte
 * (Phantom "5J4ZBT 45PQH NB60" ging so durch). */
static ftx_callsign_hash_interface_t s_hash_if_readonly = {
    .lookup_hash = shim_lookup_hash,
    .save_hash   = shim_save_hash_noop,
};

/* Optional API: aus Python pre-populate (z.B. aus DB worked-Calls).
 * Liefert die aktuelle Anzahl belegter Slots zurueck. */
int ft8_shim_hash_table_save(const char* callsign, uint32_t n22)
{
    if (callsign == NULL || callsign[0] == '\0') return -1;
    shim_save_hash(callsign, n22);
    int count = 0;
    for (int i = 0; i < HASH_TABLE_SIZE; ++i) {
        if (s_hash_table[i].used) ++count;
    }
    return count;
}

/* Liefert die aktuelle Hash-Tabelle-Belegung fuer Debug/Status. */
int ft8_shim_hash_table_count(void)
{
    int count = 0;
    for (int i = 0; i < HASH_TABLE_SIZE; ++i) {
        if (s_hash_table[i].used) ++count;
    }
    return count;
}

#define FT8_SAMPLE_RATE_HZ 12000
#define FT8_SLOT_SECONDS   15
#define FT8_SLOT_SAMPLES   (FT8_SAMPLE_RATE_HZ * FT8_SLOT_SECONDS)

/* WSJT-X-Standard verwendet ~350 Kandidaten; wir lagen bei 140
 * (~40 % weniger schwache Decodes). Pi5 hat reichlich CPU-Headroom,
 * Bump auf 300 kostet ~50-100 ms pro Slot und bringt typisch
 * 20-30 % mehr Decodes am unteren Rand (-22 .. -26 dB SNR). */
#define FT8_SHIM_MAX_CANDIDATES 1200  /* Puffergroesse; wirksam ist s_knob_max_cand */
#define FT8_SHIM_MIN_SCORE      10
#define FT8_SHIM_LDPC_ITERS     25

/* 2026-09-06: Experimentier-Knoepfe (Laufzeit, fuer Benchmarks auf dem
 * WSJT-X-Korpus). Defaults = produktives Verhalten. */
static int s_knob_sub_score   = 20;   /* Subtraktion nur fuer Decodes mit score >= */
static int s_knob_hint_osr    = 4;    /* time/freq-OSR des Hint-Passes (2026-09-06: 4, mit 2-Symbol-Fenster) */
static int s_knob_sub_rounds  = 2;    /* Subtract-Runden */
static int s_knob_min_score   = 10;   /* Kandidaten-Mindestscore std/deep */
static int s_knob_deep_ldpc   = 50;   /* LDPC-Iterationen deep */
static int s_knob_std_tosr    = 4;    /* time_osr des std-Passes (2026-09-06: 4 — Korpus 256 -> 265, +13 ms x86) */
static int s_knob_std_ldpc    = 25;   /* LDPC-Iterationen std */
static int s_knob_deep_fosr   = 4;    /* freq_osr des deep-Passes */
static int s_knob_max_cand    = 300;  /* Kandidaten pro Pass (Puffer 1200) */
static int s_knob_window_std  = 0;    /* monitor window_mode (s. monitor.h) fuer osr-2-Paesse */
static int s_knob_window_deep = 4;    /* ... fuer osr-4-Paesse: Hann 2 Symbole statt 4 (Korpus: deep 0 -> 14 Decodes) */
static int s_knob_window_hint = 4;    /* ... fuer den Hint-Pass */
static int s_knob_refine      = 1;    /* Feinsync-Demodulation im Hint-Pass nach BP/OSD-Fehlschlag */
static int s_knob_refine_max  = 40;   /* max. verfeinerte Kandidaten pro Slot */
static int s_knob_refine_nogate = 1;  /* 1: Feinsync-Decodes per BP+CRC (ohne OSD) brauchen keinen bekannten Call — so vertrauenswuerdig wie der std-Pass */
static int s_knob_llr_scales  = 1;    /* 1 = nur BP mit Original-LLR; 2..4 = zusaetzliche Skalierungen (WSJT-X: llra..llrd) */
int ft8_shim_set_knob(const char* name, int value) {
    if (strcmp(name, "sub_score") == 0) s_knob_sub_score = value;
    else if (strcmp(name, "hint_osr") == 0) s_knob_hint_osr = value;
    else if (strcmp(name, "sub_rounds") == 0) s_knob_sub_rounds = value;
    else if (strcmp(name, "min_score") == 0) s_knob_min_score = value;
    else if (strcmp(name, "deep_ldpc") == 0) s_knob_deep_ldpc = value;
    else if (strcmp(name, "std_tosr") == 0) s_knob_std_tosr = value;
    else if (strcmp(name, "std_ldpc") == 0) s_knob_std_ldpc = value;
    else if (strcmp(name, "deep_fosr") == 0) s_knob_deep_fosr = value;
    else if (strcmp(name, "window_std") == 0) s_knob_window_std = value;
    else if (strcmp(name, "window_deep") == 0) s_knob_window_deep = value;
    else if (strcmp(name, "window_hint") == 0) s_knob_window_hint = value;
    else if (strcmp(name, "llr_scales") == 0) s_knob_llr_scales = value < 1 ? 1 : (value > 4 ? 4 : value);
    else if (strcmp(name, "refine") == 0) s_knob_refine = value;
    else if (strcmp(name, "refine_max") == 0) s_knob_refine_max = value;
    else if (strcmp(name, "refine_nogate") == 0) s_knob_refine_nogate = value;
    else if (strcmp(name, "max_cand") == 0) s_knob_max_cand = value < 1 ? 1 : (value > FT8_SHIM_MAX_CANDIDATES ? FT8_SHIM_MAX_CANDIDATES : value);
    else return -1;
    return 0;
}

#define FT8_SHIM_MSG_LEN  40

typedef struct {
    char  message[FT8_SHIM_MSG_LEN];
    int   snr_db_est;     /* rough SNR estimate, similar to WSJT-X's */
    float dt_s;
    float freq_hz;        /* audio-band offset */
    int   score;          /* raw Costas sync score */
} ft8_shim_result_t;


/* Decode one 15-second slot of audio.
 *
 *   pcm        : int16 little-endian mono samples at 12000 Hz
 *   n_samples  : must be >= FT8_SLOT_SAMPLES (180000)
 *   out        : caller-allocated array
 *   max_out    : capacity of *out*
 *
 * Returns the number of decodes written to *out*, or -1 on error.
 */

/* 2026-09-06: dt-Kalibrierung. ft8_lib misst die Kandidatenzeit am ENDE
 * des Hann-Analysefensters (monitor.c: last_frame wird um subblock
 * geschoben, nfft = block * freq_osr). Der wahre Symbolstart liegt darum
 * (block/2 + nfft/2 - subblock) Samples frueher: +0,16 s bei osr 2/2,
 * +0,36 s bei osr 4/4. Gemessen mit synthetischen Slots und gegen die
 * WSJT-X-Referenzdecodes in vendor/ft8_lib/test/wav (OH3NIV ZS6S: WSJT-X
 * DT 1,0 s, wir vorher 1,60 std / 1,80 deep). Folgen der alten Werte:
 * DT-Spalte pass-abhaengig falsch, |dt|<=2,5-Filter im Hunting schief,
 * DT-Drift-Detektor um +0,66 s vorgespannt, und die Subtraktion traf die
 * Referenz nie (Feinsuche +-0,04 s gegen 0,16 s Fehler).
 *
 * Konvention: DT wie WSJT-X, d.h. relativ zum nominalen Sendestart
 * FT8_DT_ORIGIN_S nach der Slotgrenze. */
#define FT8_DT_ORIGIN_S 0.5f
#define FT4_DT_ORIGIN_S 0.5f
static inline float _dt_window_corr_s(const monitor_t* mon) {
    return ((float)mon->block_size * 0.5f + (float)mon->window_len * 0.5f - (float)mon->subblock_size)
           / 12000.0f;
}

int ft8_shim_decode_slot(
    const int16_t* pcm,
    int            n_samples,
    ft8_shim_result_t* out,
    int            max_out
) {
    if (pcm == NULL || out == NULL || max_out <= 0) {
        return -1;
    }
    if (n_samples < FT8_SLOT_SAMPLES) {
        return -1;
    }

    /* int16 -> float in [-1, 1] */
    float* signal = (float*)malloc(sizeof(float) * FT8_SLOT_SAMPLES);
    if (signal == NULL) {
        return -1;
    }
    for (int i = 0; i < FT8_SLOT_SAMPLES; ++i) {
        signal[i] = (float)pcm[i] / 32768.0f;
    }

    /* Configure monitor identically to the demo decoder */
    monitor_t mon;
    monitor_config_t cfg;
    cfg.f_min       = 200.0f;
    cfg.f_max       = 3000.0f;
    cfg.sample_rate = FT8_SAMPLE_RATE_HZ;
    cfg.time_osr    = s_knob_std_tosr;
    cfg.freq_osr    = 2;
    cfg.protocol    = FTX_PROTOCOL_FT8;
    cfg.window_mode = s_knob_window_std;
    monitor_init(&mon, &cfg);

    /* Slide through the slot accumulating the waterfall */
    for (int pos = 0; pos + mon.block_size <= FT8_SLOT_SAMPLES; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    free(signal);

    /* Find sync candidates */
    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, s_knob_max_cand, candidates, FT8_SHIM_MIN_SCORE
    );

    /* Decode each candidate; dedupe by message.hash */
    uint8_t seen[200][10];   /* 2026-09-06: volle 77-Bit-Nutzlast statt CRC-14 (Kollision 1:16384 -> pro Slot ~3 %) */
    int      num_seen = 0;
    int      num_out  = 0;

    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];

        ftx_message_t       message;
        ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, cand, s_knob_std_ldpc, &message, &status)) {
            continue;  /* LDPC fail or CRC mismatch */
        }

        int dup = 0;
        for (int j = 0; j < num_seen; ++j) {
            if (memcmp(seen[j], message.payload, 10) == 0) {
                dup = 1;
                break;
            }
        }
        if (dup) continue;
        if (num_seen < (int)(sizeof(seen) / sizeof(seen[0]))) {
            memcpy(seen[num_seen++], message.payload, 10);
        }

        /* Unpack the 77-bit payload to human-readable text. We do not pass
         * a callsign-hash interface, so non-standard (<HASH>) calls won't
         * round-trip — they'll appear with a hash placeholder. Acceptable
         * for the MVP. */
        char                   text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t  offsets;
        ftx_message_rc_t       rc = ftx_message_decode(&message, &s_hash_if, text, &offsets);
        if (rc != FTX_MESSAGE_RC_OK) {
            continue;
        }

        ft8_shim_result_t* r = &out[num_out];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';

        /* Approximation of SNR similar to WSJT-X's: score scaled down.
         * Calibration against gen_ft8 reference suggests SNR ~ score/2 - 24
         * is a usable first cut; precision is not critical for our use. */
        r->snr_db_est = (cand->score / 2) - 24;
        r->score      = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon.wf.time_osr)
                   * mon.symbol_period - _dt_window_corr_s(&mon) - FT8_DT_ORIGIN_S;
        r->freq_hz = (mon.min_bin
                      + cand->freq_offset
                      + (float)cand->freq_sub / mon.wf.freq_osr)
                     / mon.symbol_period;
        ++num_out;
    }

    monitor_free(&mon);
    return num_out;
}


/* ========================================================================= *
 * TX synthesis — message text -> 12 kHz mono int16 PCM samples.
 * ========================================================================= *
 *
 * Mirrors vendor/ft8_lib/demo/gen_ft8.c: pack text via ftx_message_encode,
 * generate 79 FSK tone symbols via ft8_encode, then GFSK-shape into a
 * waveform using the synth_gfsk function. We reimplement gfsk_pulse +
 * synth_gfsk inline because ft8_lib leaves the synthesiser in the demo.
 */

#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FT8_TONE_SPACING_HZ   6.25f
#define FT8_SYMBOL_PERIOD_S   0.16f                     /* 1 / 6.25 */
#define FT8_NUM_SYMBOLS       79
#define FT8_SAMPLES_PER_SYM   1920                      /* 12000 * 0.16 */
#define FT8_TX_SAMPLES        (FT8_NUM_SYMBOLS * FT8_SAMPLES_PER_SYM)  /* 151680 */
#define FT8_SYMBOL_BT         2.0f
#define FT8_GFSK_CONST_K      5.336446f                 /* pi * sqrt(2/log(2)) */

extern void ft8_encode(const uint8_t *payload, uint8_t *tones);
/* ftx_message_encode is declared in ft8/message.h via the included header. */


static void _gfsk_pulse(int n_spsym, float bt, float* pulse) {
    for (int i = 0; i < 3 * n_spsym; ++i) {
        float t = i / (float)n_spsym - 1.5f;
        float a1 = FT8_GFSK_CONST_K * bt * (t + 0.5f);
        float a2 = FT8_GFSK_CONST_K * bt * (t - 0.5f);
        pulse[i] = (erff(a1) - erff(a2)) / 2.0f;
    }
}


/* Synthesise one FT8 waveform of FT8_TX_SAMPLES into *signal* (float-32).
 * Reference: vendor/ft8_lib/demo/gen_ft8.c::synth_gfsk
 */
static void _synth_gfsk(const uint8_t* tones, float f0, float* signal) {
    int n_spsym = FT8_SAMPLES_PER_SYM;
    int n_wave  = FT8_NUM_SYMBOLS * n_spsym;
    int n_total = n_wave + 2 * n_spsym;

    float dphi_peak = 2.0f * (float)M_PI / n_spsym;
    float* dphi = (float*)calloc((size_t)n_total, sizeof(float));
    if (dphi == NULL) return;
    for (int i = 0; i < n_total; ++i) {
        dphi[i] = 2.0f * (float)M_PI * f0 / 12000.0f;
    }

    float pulse[3 * FT8_SAMPLES_PER_SYM];
    _gfsk_pulse(n_spsym, FT8_SYMBOL_BT, pulse);

    for (int i = 0; i < FT8_NUM_SYMBOLS; ++i) {
        int ib = i * n_spsym;
        for (int j = 0; j < 3 * n_spsym; ++j) {
            dphi[j + ib] += dphi_peak * (float)tones[i] * pulse[j];
        }
    }
    /* Extend first/last symbol phase shoulders (matches reference) */
    for (int j = 0; j < 2 * n_spsym; ++j) {
        dphi[j]                          = dphi[2 * n_spsym] - dphi_peak * (float)tones[0];
        dphi[j + FT8_NUM_SYMBOLS * n_spsym] =
            dphi[FT8_NUM_SYMBOLS * n_spsym - 1] - dphi_peak * (float)tones[FT8_NUM_SYMBOLS - 1];
    }

    float phi = 0.0f;
    for (int k = 0; k < n_wave; ++k) {
        signal[k] = sinf(phi);
        phi      += dphi[k + n_spsym];
        if (phi >= 2.0f * (float)M_PI) phi -= 2.0f * (float)M_PI;
    }

    /* Envelope ramp at edges to suppress clicks (length n_spsym/8 ≈ 20 ms) */
    int n_ramp = n_spsym / 8;
    for (int i = 0; i < n_ramp; ++i) {
        float w = 0.5f * (1.0f - cosf((float)M_PI * (float)i / n_ramp));
        signal[i]              *= w;
        signal[n_wave - 1 - i] *= w;
    }

    free(dphi);
}


/* Encode a textual FT8 message into 12000 Hz mono int16 PCM samples.
 *
 *   text          : null-terminated message string (max 35 chars + NUL)
 *   audio_freq_hz : base audio frequency in Hz (typ. 1500)
 *   amplitude     : 0..1 (scale for the int16 conversion)
 *   out_pcm       : caller-allocated int16 buffer, size >= FT8_TX_SAMPLES (151680)
 *   out_capacity  : number of int16 slots in *out_pcm*
 *
 * Returns the number of int16 samples written, or -1 on error.
 */
/* ========================================================================= *
 * Sweep-B C-Hooks — AP-Decoding and Multi-Pass+Subtract.
 *
 * These stubs land the call surface that the orchestrator will use once
 * the LDPC soft-bit pinning and signal-subtract paths are wired into
 * ft8_lib. Until then both functions delegate to the regular single-
 * pass decoder so callers can integrate against a stable API today.
 *
 * AP (a-priori) decoding:
 *   The orchestrator knows our own callsign and the call we're
 *   currently working — pinning those into the LDPC soft-decision
 *   buffer makes the decoder pull weaker decodes out of the noise
 *   (~3 dB sensitivity gain in WSJT-X). API: callsigns are passed as
 *   space-separated upper-case ASCII strings.
 *
 * Multi-Pass+Subtract:
 *   After pass 1 successfully decodes N candidates, synthesise their
 *   waveforms, subtract from the waterfall, and run pass 2 on the
 *   residual. Recovers decodes that pass 1 missed due to a stronger
 *   collider hiding them. ``num_passes`` of 1 is the legacy behaviour.
 * ========================================================================= */

int ft8_shim_decode_slot_ap(
    const int16_t* pcm,
    int            n_samples,
    const char*    ap_callsigns,   /* space-separated upper-case ASCII; may be NULL */
    int            ap_callsigns_len,
    ft8_shim_result_t* out,
    int            max_out
) {
    /* Stub: ignore the AP priors for now. The single-pass decode
     * already returns everything ft8_lib finds; once we wire soft-bit
     * pinning here, this hook becomes worthwhile. */
    (void)ap_callsigns;
    (void)ap_callsigns_len;
    return ft8_shim_decode_slot(pcm, n_samples, out, max_out);
}


int ft8_shim_decode_slot_multipass(
    const int16_t* pcm,
    int            n_samples,
    int            num_passes,
    ft8_shim_result_t* out,
    int            max_out
) {
    if (num_passes < 1) num_passes = 1;
    /* Stub: only the first pass is implemented. The subtract-and-rerun
     * loop needs us to (a) synthesise each decode at its measured
     * dt/freq, (b) line-up with the original waterfall, (c) subtract
     * before re-running ftx_find_candidates. Wiring lands in Sweep B
     * once ft8_lib exposes the residual buffer. */
    (void)num_passes;
    return ft8_shim_decode_slot(pcm, n_samples, out, max_out);
}


/* ========================================================================= *
 * v0.6.0 Anti-WSJT-X-Audit Phase B: tunable decoder with optional deep mode
 * and a Pass1+Pass2 multipass (standard + deep) with merge+dedupe.
 * Replaces the no-op stubs above for new callers; old API stays for
 * binary compatibility.
 *
 * mode:
 *   0 = standard (time_osr=2, freq_osr=2, LDPC=25) — same as decode_slot
 *   1 = deep     (time_osr=4, freq_osr=4, LDPC=50) — more CPU, ~1-3 extra
 *                 decodes/slot near the -22..-24 dB sensitivity floor
 *   2 = multi    Pass1 standard + Pass2 deep, dedupe → highest yield,
 *                 ~1.5-2x the CPU of standard. JTDX-Niveau ohne Subtract.
 *
 * Returns count of unique decodes written to *out*, -1 on error.
 * ========================================================================= */

/* v0.8.0 Build D: Adaptive LDPC-Iter-Factor — fwd-declared HIER damit
 * _ft8_decode_one_pass darauf zugreifen kann. Definition kommt unten. */
static int s_ldpc_factor_pct;


static int _osd_crc_ok(const uint8_t plain174[FTX_LDPC_N]);

/* 2026-09-06: BP mit mehreren LLR-Skalierungen. Belief Propagation ist
 * empfindlich gegen die absolute Skala der Soft-Bits; WSJT-X probiert
 * darum bis zu vier Varianten (llra..llrd) pro Kandidat. ft8_lib normiert
 * auf Varianz 24 und probiert nur diese eine. */
static bool _ft8_decode_candidate_scaled(const ftx_waterfall_t* wf, const ftx_candidate_t* cand, int max_iterations,
                                         ftx_message_t* message, ftx_decode_status_t* status) {
    if (s_knob_llr_scales <= 1) return ftx_decode_candidate(wf, cand, max_iterations, message, status);
    static const float scales[4] = { 1.0f, 0.5f, 2.0f, 0.25f };
    float log174[FTX_LDPC_N], work[FTX_LDPC_N];
    uint8_t plain174[FTX_LDPC_N];
    ftx_extract_llr(wf, cand, log174);
    for (int si = 0; si < s_knob_llr_scales; ++si) {
        for (int i = 0; i < FTX_LDPC_N; ++i) work[i] = log174[i] * scales[si];
        bp_decode(work, max_iterations, plain174, &status->ldpc_errors);
        if (status->ldpc_errors > 0) continue;
        if (!_osd_crc_ok(plain174)) continue;
        uint8_t a91[FTX_LDPC_K_BYTES]; memset(a91, 0, sizeof(a91));
        for (int i = 0; i < FTX_LDPC_K; ++i) if (plain174[i]) a91[i >> 3] |= (uint8_t)(0x80u >> (i & 7));
        message->hash = ftx_extract_crc(a91);
        a91[9] &= 0xF8;   /* wie ftx_decode_candidate: CRC-Bits raus, sonst schlaegt die Payload-Dedupe fehl */
        for (int i = 0; i < 10; ++i) message->payload[i] = a91[i];
        return true;
    }
    return false;
}

static int _ft8_decode_one_pass(
    float*             signal,
    int                signal_len,
    int                time_osr,
    int                freq_osr,
    int                ldpc_iters,
    ft8_shim_result_t* out,
    int                max_out,
    uint8_t          (*seen)[10],
    int*               num_seen,
    int                num_out_initial
) {
    monitor_t mon;
    monitor_config_t cfg;
    cfg.f_min       = 200.0f;
    cfg.f_max       = 3000.0f;
    cfg.sample_rate = FT8_SAMPLE_RATE_HZ;
    cfg.time_osr    = time_osr;
    cfg.freq_osr    = freq_osr;
    cfg.protocol    = FTX_PROTOCOL_FT8;
    cfg.window_mode = (time_osr >= 4 && freq_osr >= 4) ? s_knob_window_deep : s_knob_window_std;
    monitor_init(&mon, &cfg);

    for (int pos = 0; pos + mon.block_size <= signal_len; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }

    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, s_knob_max_cand, candidates, s_knob_min_score
    );

    /* v0.8.0 Build D: adaptive LDPC-Iter via global factor (Pipeline-set) */
    int eff_ldpc_iters = (ldpc_iters * s_ldpc_factor_pct) / 100;
    if (eff_ldpc_iters < 5) eff_ldpc_iters = 5;
    int num_out = num_out_initial;
    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];

        ftx_message_t       message;
        ftx_decode_status_t status;
        if (!_ft8_decode_candidate_scaled(&mon.wf, cand, eff_ldpc_iters, &message, &status)) {
            continue;
        }

        int dup = 0;
        for (int j = 0; j < *num_seen; ++j) {
            if (memcmp(seen[j], message.payload, 10) == 0) { dup = 1; break; }
        }
        if (dup) continue;
        if (*num_seen < 200) memcpy(seen[(*num_seen)++], message.payload, 10);

        char                   text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t  offsets;
        ftx_message_rc_t       rc = ftx_message_decode(&message, &s_hash_if, text, &offsets);
        if (rc != FTX_MESSAGE_RC_OK) continue;

        ft8_shim_result_t* r = &out[num_out];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';
        r->snr_db_est = (cand->score / 2) - 24;
        r->score      = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon.wf.time_osr)
                   * mon.symbol_period - _dt_window_corr_s(&mon) - FT8_DT_ORIGIN_S;
        r->freq_hz = (mon.min_bin
                      + cand->freq_offset
                      + (float)cand->freq_sub / mon.wf.freq_osr)
                     / mon.symbol_period;
        ++num_out;
    }

    monitor_free(&mon);
    return num_out;
}


/* v0.8.0 Build C: Per-Pass Decoder-Statistics.
 *
 * Wir tracken pro decoder-mode-call wieviele Decodes JEDER Pass-Type
 * liefert. Hilft Sebastian zu sehen welcher Pass tatsaechlich Mehrwert
 * bringt:
 *   - standard / deep / multi / extreme: Pass-Type counts
 *   - subtract_pass: Decodes nach subtract+rerun (mode=extreme only)
 *   - hint_pass:     Decodes vom Hint-Decoder mit known-call match
 *
 * Cumulative seit Service-Start. Reset moeglich via _reset Funktion.
 */
typedef struct {
    uint64_t pass_standard;
    uint64_t pass_deep;
    uint64_t pass_subtract_residual;
    uint64_t pass_hint;
    uint64_t slots_decoded;
    /* 2026-09-06: zweite Subtract-Runde (JTDX macht 2-3) */
    uint64_t pass_subtract_round2;
    uint64_t pass_osd;   /* 2026-09-06: Hint-Pass-Decodes, die erst OSD geliefert hat */
    uint64_t pass_refine; /* 2026-09-06: Decodes erst nach Feinsync-Demodulation */
} ft8_shim_pass_stats_t;

static ft8_shim_pass_stats_t s_pass_stats = {0, 0, 0, 0, 0};

/* v0.8.0 Build D: Adaptive LDPC-Iter-Factor.
 *
 * Pipeline misst avg decoder-duration und setzt diesen Wert pro Slot.
 *  100 = Standard (unchanged)
 *  >100 = mehr Iter (CPU-Reserve, mehr marginal-decodes)
 *  <100 = weniger Iter (CPU-Druck, schneller)
 * Range geclamped 30..400 fuer Sanity. */
/* s_ldpc_factor_pct: fwd-declared oben bei _ft8_decode_one_pass.
 * Hier setzen wir den Default-Wert + die public-Setter/Getter. */
static int s_ldpc_factor_pct = 100;

void ft8_shim_set_ldpc_factor(int pct) {
    if (pct < 30) pct = 30;
    if (pct > 400) pct = 400;
    s_ldpc_factor_pct = pct;
}

int ft8_shim_get_ldpc_factor(void) { return s_ldpc_factor_pct; }


void ft8_shim_pass_stats_reset(void) {
    s_pass_stats.pass_standard = 0;
    s_pass_stats.pass_deep = 0;
    s_pass_stats.pass_subtract_residual = 0;
    s_pass_stats.pass_hint = 0;
    s_pass_stats.slots_decoded = 0;
    s_pass_stats.pass_subtract_round2 = 0;
    s_pass_stats.pass_osd = 0;
    s_pass_stats.pass_refine = 0;
}

void ft8_shim_pass_stats_get(ft8_shim_pass_stats_t* out) {
    if (out != NULL) *out = s_pass_stats;
}


/* v0.7.0 Anti-WSJT-X Build 1: Subtract helper. Synthesize the decoded
 * message at its measured (freq, dt) and subtract from the signal in
 * place. After all strong decodes are subtracted, a re-decode pass on
 * the residual surfaces weaker signals that were previously masked by
 * stronger ones in the same audio bin. This is the JTDX-style move.
 *
 * Amplitude estimate: we don't know the true RF amplitude, so we
 * subtract a conservative 0.4. Over-subtract introduces phase ghosts;
 * under-subtract leaves residual energy. 0.4 is a compromise that
 * works empirically (WSPR-style decoder uses similar). */
/* v0.7.0 Build 2: Hint-Decoder Helper.
 *
 * Prüft ob ein decoded Message-Text einen Call aus der s_hash_table
 * enthält (= known via worked-set oder recent-decoded). Wird im hint-
 * pass benutzt, um marginal-LDPC-Decodes (sehr niedriger Score) nur
 * dann zu akzeptieren, wenn sie eine plausible Verbindung haben.
 *
 * JTDX-Style: erlaubt 4 zusätzliche Pässe mit niedriger min_score-
 * Schwelle aber strenger Post-Validation. Hebt Decode-Sensitivity um
 * ~1-2 dB ohne explosive False-Positive-Rate.
 */
static bool _ft8_text_has_known_call(const char* text) {
    if (text == NULL) return false;
    /* Tokenisiere — max 5 tokens, max 13 chars (FT8-Limit). */
    char buf[FTX_MAX_MESSAGE_LENGTH];
    strncpy(buf, text, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char* tok = strtok(buf, " ");
    int n_checked = 0;
    while (tok != NULL && n_checked < 8) {
        /* nur Tokens die wie ein Callsign aussehen (>=3 chars, alphanumerisch + slash) */
        size_t len = strlen(tok);
        if (len >= 3 && len <= 13) {
            for (int i = 0; i < HASH_TABLE_SIZE; ++i) {
                if (!s_hash_table[i].used) continue;
                if (strncmp(s_hash_table[i].call, tok, sizeof(s_hash_table[i].call)) == 0) {
                    return true;
                }
            }
        }
        tok = strtok(NULL, " ");
        n_checked++;
    }
    return false;
}



/* ======================================================================
 * 2026-09-06: OSD — ordered statistics decoding (nach Fossorier/Lin, wie
 * WSJT-X osd174_91.f90). Wenn Belief Propagation scheitert, nehmen wir
 * die 91 zuverlaessigsten, linear unabhaengigen Codewort-Positionen als
 * Informationsmenge, loesen das Generator-System darauf und probieren
 * Ordnung 1 (alle Einzel-Flips) plus Ordnung 2 auf den unsichersten
 * Informationsbits. Ein OSD-Ergebnis ist immer ein gueltiges Codewort —
 * einziger Pruefstein ist die CRC-14 (1/16384 pro Versuch). Darum wird
 * OSD nur im Hint-Pass verwendet und dort zusaetzlich durch das Known-
 * Call-Gate abgesichert (kein Phantom ohne bekanntes Rufzeichen).
 * ====================================================================== */

typedef struct { uint64_t w[2]; } bits91_t;   /* 91 Bits: w[0] Bits 0..63, w[1] 64..90 */

static inline int _b91_get(const bits91_t* b, int k) { return (int)((b->w[k >> 6] >> (k & 63)) & 1u); }
static inline void _b91_set(bits91_t* b, int k) { b->w[k >> 6] |= (uint64_t)1 << (k & 63); }
static inline int _b91_parity_and(const bits91_t* a, const bits91_t* b) {
    return (int)((__builtin_popcountll(a->w[0] & b->w[0]) + __builtin_popcountll(a->w[1] & b->w[1])) & 1);
}
static inline int _b91_zero(const bits91_t* b) { return b->w[0] == 0 && b->w[1] == 0; }

static int s_osd_depth = 2;           /* 0 aus, 1 Ordnung 1, 2 Ordnung 1+2 (teilweise) */
static int s_osd_order2_tail = 60;    /* Ordnung 2 nur ueber die unsichersten N Infobits */
static int s_osd_crc_tries = 16;      /* beste Kandidaten nach Metrik, die eine CRC-Pruefung bekommen */
#define OSD_CRC_TRIES_MAX 32
static int s_osd_last_nhard = 0;      /* Diagnose: Hamming-Abstand harte Entscheidung <-> Codewort */
static float s_osd_last_metric = 0.0f;
void ft8_shim_set_osd_params(int order2_tail, int crc_tries) {
    s_osd_order2_tail = order2_tail < 0 ? 0 : (order2_tail > FTX_LDPC_K ? FTX_LDPC_K : order2_tail);
    s_osd_crc_tries = crc_tries < 1 ? 1 : (crc_tries > OSD_CRC_TRIES_MAX ? OSD_CRC_TRIES_MAX : crc_tries);
}

void ft8_shim_set_osd_depth(int depth) { s_osd_depth = depth < 0 ? 0 : (depth > 2 ? 2 : depth); }
int  ft8_shim_get_osd_depth(void) { return s_osd_depth; }

/* Generatorzeile fuer Codewort-Position n (0..173) als 91-Bit-Vektor ueber
 * die Nachrichtenbits: Identitaet fuer n<91, sonst kFTX_LDPC_generator. */
static void _osd_gen_row(int n, bits91_t* out) {
    out->w[0] = out->w[1] = 0;
    if (n < FTX_LDPC_K) { _b91_set(out, n); return; }
    const uint8_t* row = kFTX_LDPC_generator[n - FTX_LDPC_K];
    for (int k = 0; k < FTX_LDPC_K; ++k) {
        if ((row[k >> 3] >> (7 - (k & 7))) & 1) _b91_set(out, k);
    }
}

static int _osd_crc_ok(const uint8_t plain174[FTX_LDPC_N]) {
    uint8_t a91[FTX_LDPC_K_BYTES];
    memset(a91, 0, sizeof(a91));
    for (int i = 0; i < FTX_LDPC_K; ++i) {
        if (plain174[i]) a91[i >> 3] |= (uint8_t)(0x80u >> (i & 7));
    }
    uint16_t crc_ext = ftx_extract_crc(a91);
    a91[9] &= 0xF8; a91[10] &= 0x00;
    return ftx_compute_crc(a91, 96 - 14) == crc_ext;
}

typedef struct { float metric; int i, j; } osd_cand_t;

/* Liefert 1 und plain174, wenn ein Codewort mit gueltiger CRC gefunden wurde. */
static int _osd_decode(const float* llr, int depth, uint8_t plain174[FTX_LDPC_N]) {
    static bits91_t gen[FTX_LDPC_N];
    static int gen_ready = 0;
    if (!gen_ready) { for (int n = 0; n < FTX_LDPC_N; ++n) _osd_gen_row(n, &gen[n]); gen_ready = 1; }

    uint8_t hard[FTX_LDPC_N]; float rel[FTX_LDPC_N]; int order[FTX_LDPC_N];
    for (int n = 0; n < FTX_LDPC_N; ++n) { hard[n] = llr[n] > 0.0f; rel[n] = fabsf(llr[n]); order[n] = n; }
    for (int a = 1; a < FTX_LDPC_N; ++a) {
        int v = order[a]; int b = a - 1;
        while (b >= 0 && rel[order[b]] < rel[v]) { order[b + 1] = order[b]; --b; }
        order[b + 1] = v;
    }

    /* 91 unabhaengige, moeglichst zuverlaessige Positionen einsammeln. */
    bits91_t rv[FTX_LDPC_K], rw[FTX_LDPC_K]; int sel[FTX_LDPC_K]; int count = 0;
    bits91_t piv_v[FTX_LDPC_K]; int piv_used[FTX_LDPC_K];
    memset(piv_used, 0, sizeof(piv_used));
    for (int a = 0; a < FTX_LDPC_N && count < FTX_LDPC_K; ++a) {
        int n = order[a];
        bits91_t v = gen[n];
        for (int p = 0; p < FTX_LDPC_K; ++p) {
            if (piv_used[p] && _b91_get(&v, p)) { v.w[0] ^= piv_v[p].w[0]; v.w[1] ^= piv_v[p].w[1]; }
        }
        if (_b91_zero(&v)) continue;
        int p = v.w[0] ? __builtin_ctzll(v.w[0]) : 64 + __builtin_ctzll(v.w[1]);
        piv_v[p] = v; piv_used[p] = 1;
        rv[count] = gen[n]; rw[count].w[0] = rw[count].w[1] = 0; _b91_set(&rw[count], count);
        sel[count] = n; ++count;
    }
    if (count < FTX_LDPC_K) return 0;

    /* Gauss-Jordan auf [R | I] -> [I | R^-1]; rw[i] = Zeile i von R^-1 */
    for (int c = 0; c < FTX_LDPC_K; ++c) {
        int r = -1;
        for (int k = c; k < FTX_LDPC_K; ++k) if (_b91_get(&rv[k], c)) { r = k; break; }
        if (r < 0) return 0;
        if (r != c) { bits91_t t = rv[r]; rv[r] = rv[c]; rv[c] = t; t = rw[r]; rw[r] = rw[c]; rw[c] = t; }
        for (int k = 0; k < FTX_LDPC_K; ++k) {
            if (k != c && _b91_get(&rv[k], c)) { rv[k].w[0] ^= rv[c].w[0]; rv[k].w[1] ^= rv[c].w[1]; rw[k].w[0] ^= rw[c].w[0]; rw[k].w[1] ^= rw[c].w[1]; }
        }
    }

    /* Harte Entscheidungen auf der Informationsmenge -> Nachricht m0 -> Codewort c0 */
    bits91_t hs; hs.w[0] = hs.w[1] = 0;
    for (int i = 0; i < FTX_LDPC_K; ++i) if (hard[sel[i]]) _b91_set(&hs, i);
    bits91_t m0; m0.w[0] = m0.w[1] = 0;
    for (int p = 0; p < FTX_LDPC_K; ++p) if (_b91_parity_and(&rw[p], &hs)) _b91_set(&m0, p);
    uint8_t c0[FTX_LDPC_N]; float d0 = 0.0f;
    for (int n = 0; n < FTX_LDPC_N; ++n) { c0[n] = (uint8_t)_b91_parity_and(&gen[n], &m0); if (c0[n] != hard[n]) d0 += rel[n]; }

    /* Flip-Codewoerter f_i = G * (R^-1 e_i) */
    static uint8_t f[FTX_LDPC_K][FTX_LDPC_N];
    for (int i = 0; i < FTX_LDPC_K; ++i) {
        bits91_t d; d.w[0] = d.w[1] = 0;
        for (int p = 0; p < FTX_LDPC_K; ++p) if (_b91_get(&rw[p], i)) _b91_set(&d, p);
        for (int n = 0; n < FTX_LDPC_N; ++n) f[i][n] = (uint8_t)_b91_parity_and(&gen[n], &d);
    }
    osd_cand_t best[OSD_CRC_TRIES_MAX]; int nbest = 0; const int OSD_CRC_TRIES = s_osd_crc_tries;
    #define OSD_PUSH(M, I, J) do { \
        if (nbest < OSD_CRC_TRIES || (M) < best[nbest - 1].metric) { \
            int _k = (nbest < OSD_CRC_TRIES) ? nbest++ : nbest - 1; \
            best[_k].metric = (M); best[_k].i = (I); best[_k].j = (J); \
            while (_k > 0 && best[_k].metric < best[_k - 1].metric) { osd_cand_t _t = best[_k]; best[_k] = best[_k - 1]; best[_k - 1] = _t; --_k; } \
        } } while (0)
    OSD_PUSH(d0, -1, -1);
    float gain[FTX_LDPC_K];
    for (int i = 0; i < FTX_LDPC_K; ++i) {
        float g = 0.0f;
        for (int n = 0; n < FTX_LDPC_N; ++n) if (f[i][n]) g += (c0[n] == hard[n]) ? rel[n] : -rel[n];
        gain[i] = g;
        if (depth >= 1) OSD_PUSH(d0 + g, i, -1);
    }
    if (depth >= 2) {
        int lo = FTX_LDPC_K - s_osd_order2_tail; if (lo < 0) lo = 0;
        for (int i = lo; i < FTX_LDPC_K; ++i) {
            for (int j = i + 1; j < FTX_LDPC_K; ++j) {
                float g = gain[i] + gain[j];
                for (int n = 0; n < FTX_LDPC_N; ++n) {
                    if (f[i][n] && f[j][n]) g -= 2.0f * ((c0[n] == hard[n]) ? rel[n] : -rel[n]);
                }
                OSD_PUSH(d0 + g, i, j);
            }
        }
    }
    #undef OSD_PUSH
    for (int k = 0; k < nbest; ++k) {
        for (int n = 0; n < FTX_LDPC_N; ++n) {
            uint8_t b = c0[n];
            if (best[k].i >= 0) b ^= f[best[k].i][n];
            if (best[k].j >= 0) b ^= f[best[k].j][n];
            plain174[n] = b;
        }
        if (_osd_crc_ok(plain174)) {
            int nh = 0; for (int n = 0; n < FTX_LDPC_N; ++n) nh += (plain174[n] != hard[n]);
            s_osd_last_nhard = nh; s_osd_last_metric = best[k].metric;
            return 1;
        }
    }
    return 0;
}

/* Wie ftx_decode_candidate (FT8), aber mit OSD-Fallback nach BP-Fehlschlag. */
static bool _ft8_decode_candidate_osd(const ftx_waterfall_t* wf, const ftx_candidate_t* cand, int max_iterations,
                                      ftx_message_t* message, int* via_osd) {
    float log174[FTX_LDPC_N];
    uint8_t plain174[FTX_LDPC_N];
    int ldpc_errors = 0;
    *via_osd = 0;
    ftx_extract_llr(wf, cand, log174);
    bp_decode(log174, max_iterations, plain174, &ldpc_errors);
    if (ldpc_errors > 0) {
        if (s_osd_depth <= 0 || !_osd_decode(log174, s_osd_depth, plain174)) return false;
        *via_osd = 1;
    }
    if (!_osd_crc_ok(plain174)) return false;
    uint8_t a91[FTX_LDPC_K_BYTES]; memset(a91, 0, sizeof(a91));
    for (int i = 0; i < FTX_LDPC_K; ++i) if (plain174[i]) a91[i >> 3] |= (uint8_t)(0x80u >> (i & 7));
    message->hash = ftx_extract_crc(a91);
    a91[9] &= 0xF8;   /* s.o. */
    for (int i = 0; i < 10; ++i) message->payload[i] = a91[i];
    return true;
}


/* ======================================================================
 * 2026-09-06: Feinsynchronisation + symbolsynchrone Demodulation
 * (nach dem Muster von WSJT-X ft8b: sync8 verfeinert dt/f, dann DFT pro
 * Symbol). ft8_lib holt die Soft-Bits aus dem Wasserfall-Raster: bei
 * osr 4 liegt der wahre Symbolstart bis zu 0,02 s und die Frequenz bis
 * zu 0,78 Hz daneben — fuer schwache Signale kostet das Energie. Hier:
 * Raster 9x9 um den Kandidaten (60 Samples / 0,2 Hz), Costas-Energie als
 * Mass, dann 79 Symbole x 8 Toene mit Rechteckfenster ueber genau ein
 * Symbol (orthogonale Toene), LLR wie ft8_extract_symbol, BP + OSD.
 * ====================================================================== */


/* Leistung der 8 Toene eines Symbols ab Sample s0 bei Basisfrequenz f0 (Hz). */
static void _refine_tone_powers(const float* signal, int s0, float f0, float pw[8]) {
    for (int t = 0; t < 8; ++t) {
        double f = f0 + 6.25 * t;
        double c = cos(2.0 * M_PI * f / 12000.0), s = sin(2.0 * M_PI * f / 12000.0);
        double pi_ = 1.0, pq_ = 0.0, ai = 0.0, aq = 0.0;
        for (int n = 0; n < FT8_SAMPLES_PER_SYM; ++n) {
            int m = s0 + n;
            if (m >= 0 && m < FT8_SLOT_SAMPLES) { ai += signal[m] * pi_; aq -= signal[m] * pq_; }
            double tmp = pi_ * c - pq_ * s; pq_ = pi_ * s + pq_ * c; pi_ = tmp;
        }
        pw[t] = (float)(ai * ai + aq * aq);
    }
}

/* Costas-Energie: Summe der Leistung des erwarteten Tons ueber die 21 Sync-Symbole. */
static double _refine_sync_energy(const float* signal, int s0, float f0) {
    static const int sync_start[3] = { 0, 36, 72 };
    double e = 0.0;
    for (int b = 0; b < 3; ++b) {
        for (int k = 0; k < 7; ++k) {
            int sym = sync_start[b] + k;
            double f = f0 + 6.25 * kFT8_Costas_pattern[k];
            double c = cos(2.0 * M_PI * f / 12000.0), s = sin(2.0 * M_PI * f / 12000.0);
            double pi_ = 1.0, pq_ = 0.0, ai = 0.0, aq = 0.0;
            int base = s0 + sym * FT8_SAMPLES_PER_SYM;
            for (int n = 0; n < FT8_SAMPLES_PER_SYM; ++n) {
                int m = base + n;
                if (m >= 0 && m < FT8_SLOT_SAMPLES) { ai += signal[m] * pi_; aq -= signal[m] * pq_; }
                double tmp = pi_ * c - pq_ * s; pq_ = pi_ * s + pq_ * c; pi_ = tmp;
            }
            e += ai * ai + aq * aq;
        }
    }
    return e;
}

static void _refine_normalize(float* log174) {
    float sum = 0, sum2 = 0;
    for (int i = 0; i < FTX_LDPC_N; ++i) { sum += log174[i]; sum2 += log174[i] * log174[i]; }
    float inv_n = 1.0f / FTX_LDPC_N;
    float variance = (sum2 - (sum * sum * inv_n)) * inv_n;
    if (variance <= 1e-12f) return;
    float norm = sqrtf(24.0f / variance);
    for (int i = 0; i < FTX_LDPC_N; ++i) log174[i] *= norm;
}

/* Liefert true + message, wenn nach Feinsync ein Codewort gefunden wurde. */
static bool _ft8_refine_decode(const float* signal, float dt_s, float freq_hz, int max_iterations,
                               ftx_message_t* message, int* via_osd) {
    int t0 = (int)lrintf((dt_s + FT8_DT_ORIGIN_S) * (float)FT8_SAMPLE_RATE_HZ);
    int best_dt = 0; float best_df = 0.0f; double best_e = -1.0;
    for (int a = -4; a <= 4; ++a) {
        for (int b = -4; b <= 4; ++b) {
            double e = _refine_sync_energy(signal, t0 + a * 60, freq_hz + (float)b * 0.2f);
            if (e > best_e) { best_e = e; best_dt = a * 60; best_df = (float)b * 0.2f; }
        }
    }
    int s0 = t0 + best_dt; float f0 = freq_hz + best_df;
    float log174[FTX_LDPC_N];
    for (int k = 0; k < FT8_ND; ++k) {
        int sym_idx = k + ((k < 29) ? 7 : 14);
        float pw[8], s2[8];
        _refine_tone_powers(signal, s0 + sym_idx * FT8_SAMPLES_PER_SYM, f0, pw);
        for (int j = 0; j < 8; ++j) s2[j] = 10.0f * log10f(1e-12f + pw[kFT8_Gray_map[j]]);
        float* l = log174 + 3 * k;
        l[0] = fmaxf(fmaxf(s2[4], s2[5]), fmaxf(s2[6], s2[7])) - fmaxf(fmaxf(s2[0], s2[1]), fmaxf(s2[2], s2[3]));
        l[1] = fmaxf(fmaxf(s2[2], s2[3]), fmaxf(s2[6], s2[7])) - fmaxf(fmaxf(s2[0], s2[1]), fmaxf(s2[4], s2[5]));
        l[2] = fmaxf(fmaxf(s2[1], s2[3]), fmaxf(s2[5], s2[7])) - fmaxf(fmaxf(s2[0], s2[2]), fmaxf(s2[4], s2[6]));
    }
    _refine_normalize(log174);
    uint8_t plain174[FTX_LDPC_N]; int ldpc_errors = 0;
    *via_osd = 0;
    bp_decode(log174, max_iterations, plain174, &ldpc_errors);
    if (ldpc_errors > 0) {
        if (s_osd_depth <= 0 || !_osd_decode(log174, s_osd_depth, plain174)) return false;
        *via_osd = 1;
    }
    if (!_osd_crc_ok(plain174)) return false;
    uint8_t a91[FTX_LDPC_K_BYTES]; memset(a91, 0, sizeof(a91));
    for (int i = 0; i < FTX_LDPC_K; ++i) if (plain174[i]) a91[i >> 3] |= (uint8_t)(0x80u >> (i & 7));
    message->hash = ftx_extract_crc(a91);
    a91[9] &= 0xF8;
    for (int i = 0; i < 10; ++i) message->payload[i] = a91[i];
    return true;
}

static int _ft8_hint_pass(
    monitor_t*         mon,
    const float*       signal,   /* fuer die Feinsync-Demodulation (NULL = aus) */
    ft8_shim_result_t* out,
    int                max_out,
    uint8_t          (*seen)[10],
    int*               num_seen,
    int                num_out_initial
) {
    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    /* min_score=5 (vs 10 Standard): viel mehr Candidates */
    int num_cand = ftx_find_candidates(
        &mon->wf, s_knob_max_cand, candidates, 5
    );
    int num_out = num_out_initial;
    int n_refined = 0;
    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];
        ftx_message_t       message;
        ftx_decode_status_t status;
        /* LDPC=120 (vs 25 Standard): mehr Iterationen für marginale Signale */
        int via_osd = 0, via_refine = 0;
        (void)status;
        if (!_ft8_decode_candidate_osd(&mon->wf, cand, 120, &message, &via_osd)) {
            if (signal == NULL || s_knob_refine <= 0 || n_refined >= s_knob_refine_max) continue;
            ++n_refined;
            float c_dt = (cand->time_offset + (float)cand->time_sub / mon->wf.time_osr) * mon->symbol_period
                         - _dt_window_corr_s(mon) - FT8_DT_ORIGIN_S;
            float c_f = (mon->min_bin + cand->freq_offset + (float)cand->freq_sub / mon->wf.freq_osr) / mon->symbol_period;
            if (!_ft8_refine_decode(signal, c_dt, c_f, 120, &message, &via_osd)) continue;
            via_refine = 1;
        }

        int dup = 0;
        for (int j = 0; j < *num_seen; ++j) {
            if (memcmp(seen[j], message.payload, 10) == 0) { dup = 1; break; }
        }
        if (dup) continue;

        char text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t offsets;
        if (ftx_message_decode(&message, &s_hash_if_readonly, text, &offsets) != FTX_MESSAGE_RC_OK) continue;

        /* Hint-Gate: nur akzeptieren wenn ein VORHER bekannter Call drin ist */
        if (!_ft8_text_has_known_call(text) && !(s_knob_refine_nogate && via_refine && !via_osd)) continue;
        /* OSD-Ergebnisse sind immer gueltige Codewoerter; zusaetzlich zur CRC
         * verlangen wir Naehe zur harten Entscheidung (Phantom: nhard 37 / 80). */
        if (via_osd && (s_osd_last_nhard > 32 || s_osd_last_metric > 60.0f)) continue;
        /* jetzt regulaer entpacken, damit die Calls in die Tabelle kommen */
        ftx_message_decode(&message, &s_hash_if, text, &offsets);
        if (via_refine) s_pass_stats.pass_refine++;
        if (via_osd) {
            s_pass_stats.pass_osd++;
            if (getenv("FT8_SHIM_OSD_DEBUG") != NULL)
                fprintf(stderr, "OSD-DEBUG %s | nhard=%d metric=%.1f score=%d\n", text, s_osd_last_nhard, s_osd_last_metric, cand->score);
        }

        if (*num_seen < 200) memcpy(seen[(*num_seen)++], message.payload, 10);

        ft8_shim_result_t* r = &out[num_out];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';
        r->snr_db_est = (cand->score / 2) - 24;
        r->score      = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon->wf.time_osr)
                   * mon->symbol_period - _dt_window_corr_s(mon) - FT8_DT_ORIGIN_S;
        r->freq_hz = (mon->min_bin
                      + cand->freq_offset
                      + (float)cand->freq_sub / mon->wf.freq_osr)
                     / mon->symbol_period;
        ++num_out;
    }
    return num_out;
}


/* Helper für mode=3: Hint-Pass benötigt einen fertigen monitor_t aus
 * dem letzten Decode-Schritt. Da `_ft8_decode_one_pass` den Monitor
 * lokal allokiert + freed, wrappen wir Hint inline mit eigenem
 * monitor in `_ft8_hint_pass_signal` (init + process + Hint-Pass). */
static int _ft8_hint_pass_signal(
    float*             signal,
    int                signal_len,
    int                time_osr,
    int                freq_osr,
    ft8_shim_result_t* out,
    int                max_out,
    uint8_t          (*seen)[10],
    int*               num_seen,
    int                num_out_initial
) {
    monitor_t mon;
    monitor_config_t cfg;
    cfg.f_min       = 200.0f;
    cfg.f_max       = 3000.0f;
    cfg.sample_rate = FT8_SAMPLE_RATE_HZ;
    cfg.time_osr    = time_osr;
    cfg.freq_osr    = freq_osr;
    cfg.protocol    = FTX_PROTOCOL_FT8;
    cfg.window_mode = s_knob_window_hint;
    monitor_init(&mon, &cfg);
    for (int pos = 0; pos + mon.block_size <= signal_len; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    int n = _ft8_hint_pass(&mon, signal, out, max_out, seen, num_seen, num_out_initial);
    monitor_free(&mon);
    return n;
}


/* 2026-09-06: kohaerente Subtraktion (nach dem Muster von WSJT-X
 * subtractft8.f90). Die Vorgaengerversion zog eine synthetische GFSK-
 * Welle mit fester Amplitude 0,4 und zufaelliger Phase ab — das war
 * keine Subtraktion, sondern ein zweiter Stoerer. Im Benchmark (starke
 * Signale 30 dB ueber schwachen Nachbarn in 20-30 Hz Abstand) fand der
 * Residual-Pass danach exakt null zusaetzliche Decodes.
 *
 * Jetzt:
 *   1. komplexe Referenz e^{j phi(t)} des decodierten Signals synthetisieren
 *   2. Feinsuche ueber Frequenz (+-1,5 Hz) und Zeit (+-0,04 s), weil der
 *      Decoder nur auf 3,125 Hz / 0,08 s aufloest
 *   3. komplexe Amplitude A(t) = 2 conj(<x * ref>) gleitend ueber ~0,33 s
 *      schaetzen (Betrag UND Phase, zeitvariant -> Fading mitgenommen)
 *   4. Re(A(t) * ref(t)) abziehen
 * Ein Fehl-Decode ergibt A ~ 0 und zieht praktisch nichts ab. */

#define SUB_WIN        4000   /* Glaettungsfenster in Samples (WSJT-X: nfilt=4000) */
#define SUB_N_DF       5
#define SUB_N_DT       5

static void _synth_gfsk_iq(const uint8_t* tones, float f0, float* out_i, float* out_q) {
    int n_spsym = FT8_SAMPLES_PER_SYM;
    int n_wave  = FT8_NUM_SYMBOLS * n_spsym;
    int n_total = n_wave + 2 * n_spsym;
    float dphi_peak = 2.0f * (float)M_PI / n_spsym;
    float* dphi = (float*)calloc((size_t)n_total, sizeof(float));
    if (dphi == NULL) return;
    for (int i = 0; i < n_total; ++i) dphi[i] = 2.0f * (float)M_PI * f0 / 12000.0f;
    float pulse[3 * FT8_SAMPLES_PER_SYM];
    _gfsk_pulse(n_spsym, FT8_SYMBOL_BT, pulse);
    for (int i = 0; i < FT8_NUM_SYMBOLS; ++i) {
        int ib = i * n_spsym;
        for (int j = 0; j < 3 * n_spsym; ++j) dphi[j + ib] += dphi_peak * (float)tones[i] * pulse[j];
    }
    for (int j = 0; j < 2 * n_spsym; ++j) {
        dphi[j] = dphi[2 * n_spsym] - dphi_peak * (float)tones[0];
        dphi[j + FT8_NUM_SYMBOLS * n_spsym] =
            dphi[FT8_NUM_SYMBOLS * n_spsym - 1] - dphi_peak * (float)tones[FT8_NUM_SYMBOLS - 1];
    }
    float phi = 0.0f;
    for (int k = 0; k < n_wave; ++k) {
        out_i[k] = cosf(phi);
        out_q[k] = sinf(phi);
        phi += dphi[k + n_spsym];
        if (phi >= 2.0f * (float)M_PI) phi -= 2.0f * (float)M_PI;
    }
    free(dphi);
}

/* Energie, die ein gleitendes Fenster der Laenge SUB_WIN aus x*ref
 * kohaerent einsammelt — das Mass fuer "Referenz passt". Mit
 * Frequenzversatz df (als Phasor-Rekurrenz) und Zeitversatz ds. */
static double _sub_coherence(const float* x, int x_len, const float* ri, const float* rq,
                             int start, float df) {
    double ci = cos(2.0 * M_PI * df / 12000.0), cq = sin(2.0 * M_PI * df / 12000.0);
    double pi_ = 1.0, pq_ = 0.0;   /* laufender Phasor e^{j 2 pi df n / fs} */
    double acc_i = 0.0, acc_q = 0.0, energy = 0.0;
    int cnt = 0;
    for (int n = 0; n < FT8_TX_SAMPLES; ++n) {
        int m = start + n;
        double rri = ri[n] * pi_ - rq[n] * pq_;   /* ref * phasor */
        double rrq = ri[n] * pq_ + rq[n] * pi_;
        double t = pi_ * ci - pq_ * cq; pq_ = pi_ * cq + pq_ * ci; pi_ = t;
        if (m < 0 || m >= x_len) continue;
        acc_i += x[m] * rri; acc_q += x[m] * rrq; ++cnt;
        if (cnt == SUB_WIN) {
            energy += acc_i * acc_i + acc_q * acc_q;
            acc_i = acc_q = 0.0; cnt = 0;
        }
    }
    if (cnt > SUB_WIN / 2) energy += (acc_i * acc_i + acc_q * acc_q) * ((double)SUB_WIN / cnt);
    return energy;
}

static void _ft8_subtract_decoded(
    float*      signal,
    const char* text,
    float       freq_hz,
    float       dt_s
) {
    ftx_message_t msg;
    if (ftx_message_encode(&msg, &s_hash_if, text) != FTX_MESSAGE_RC_OK) return;
    uint8_t tones[FT8_NUM_SYMBOLS];
    ft8_encode(msg.payload, tones);

    float* ri = (float*)malloc(sizeof(float) * FT8_TX_SAMPLES);
    float* rq = (float*)malloc(sizeof(float) * FT8_TX_SAMPLES);
    float* zi = (float*)malloc(sizeof(float) * (FT8_TX_SAMPLES + 1));
    float* zq = (float*)malloc(sizeof(float) * (FT8_TX_SAMPLES + 1));
    if (ri == NULL || rq == NULL || zi == NULL || zq == NULL) { free(ri); free(rq); free(zi); free(zq); return; }
    _synth_gfsk_iq(tones, freq_hz, ri, rq);

    int start0 = (int)lrintf((dt_s + FT8_DT_ORIGIN_S) * (float)FT8_SAMPLE_RATE_HZ);
    static const float dfs[SUB_N_DF] = { -1.5f, -0.75f, 0.0f, 0.75f, 1.5f };
    static const int   dss[SUB_N_DT] = { -480, -240, 0, 240, 480 };
    float best_df = 0.0f; int best_ds = 0; double best_e = -1.0;
    for (int a = 0; a < SUB_N_DF; ++a) {
        for (int b = 0; b < SUB_N_DT; ++b) {
            double e = _sub_coherence(signal, FT8_SLOT_SAMPLES, ri, rq, start0 + dss[b], dfs[a]);
            if (e > best_e) { best_e = e; best_df = dfs[a]; best_ds = dss[b]; }
        }
    }
    int start = start0 + best_ds;

    /* Referenz auf die feine Frequenz drehen, dann z = x * ref und
     * Praefixsummen fuer das gleitende Fenster. */
    {
        double ci = cos(2.0 * M_PI * best_df / 12000.0), cq = sin(2.0 * M_PI * best_df / 12000.0);
        double pi_ = 1.0, pq_ = 0.0;
        for (int n = 0; n < FT8_TX_SAMPLES; ++n) {
            float rri = (float)(ri[n] * pi_ - rq[n] * pq_);
            float rrq = (float)(ri[n] * pq_ + rq[n] * pi_);
            ri[n] = rri; rq[n] = rrq;
            double t = pi_ * ci - pq_ * cq; pq_ = pi_ * cq + pq_ * ci; pi_ = t;
        }
    }
    zi[0] = 0.0f; zq[0] = 0.0f;
    for (int n = 0; n < FT8_TX_SAMPLES; ++n) {
        int m = start + n;
        float x = (m >= 0 && m < FT8_SLOT_SAMPLES) ? signal[m] : 0.0f;
        zi[n + 1] = zi[n] + x * ri[n];
        zq[n + 1] = zq[n] + x * rq[n];
    }
    for (int n = 0; n < FT8_TX_SAMPLES; ++n) {
        int m = start + n;
        if (m < 0 || m >= FT8_SLOT_SAMPLES) continue;
        int lo = n - SUB_WIN / 2; if (lo < 0) lo = 0;
        int hi = n + SUB_WIN / 2; if (hi > FT8_TX_SAMPLES) hi = FT8_TX_SAMPLES;
        float inv = 1.0f / (float)(hi - lo);
        float avg_i = (zi[hi] - zi[lo]) * inv;
        float avg_q = (zq[hi] - zq[lo]) * inv;
        /* A = 2 conj(avg); Re(A * ref) = 2 (avg_i * ri + avg_q * rq) */
        signal[m] -= 2.0f * (avg_i * ri[n] + avg_q * rq[n]);
    }
    free(ri); free(rq); free(zi); free(zq);
}


/* Testzugang: eine decodierte Nachricht aus einem PCM-Slot subtrahieren
 * (in place). Damit laesst sich die Guete der Subtraktion in dB messen. */
int ft8_shim_subtract_message(int16_t* pcm, int n_samples, const char* text, float freq_hz, float dt_s) {
    if (pcm == NULL || text == NULL || n_samples < FT8_SLOT_SAMPLES) return -1;
    float* signal = (float*)malloc(sizeof(float) * FT8_SLOT_SAMPLES);
    if (signal == NULL) return -1;
    for (int i = 0; i < FT8_SLOT_SAMPLES; ++i) signal[i] = (float)pcm[i] / 32768.0f;
    _ft8_subtract_decoded(signal, text, freq_hz, dt_s);
    for (int i = 0; i < FT8_SLOT_SAMPLES; ++i) {
        float v = signal[i] * 32768.0f;
        if (v > 32767.0f) v = 32767.0f;
        if (v < -32768.0f) v = -32768.0f;
        pcm[i] = (int16_t)lrintf(v);
    }
    free(signal);
    return 0;
}


int ft8_shim_decode_slot_v2(
    const int16_t*     pcm,
    int                n_samples,
    int                mode,
    ft8_shim_result_t* out,
    int                max_out
) {
    if (pcm == NULL || out == NULL || max_out <= 0) return -1;
    if (n_samples < FT8_SLOT_SAMPLES) return -1;

    float* signal = (float*)malloc(sizeof(float) * FT8_SLOT_SAMPLES);
    if (signal == NULL) return -1;
    for (int i = 0; i < FT8_SLOT_SAMPLES; ++i) {
        signal[i] = (float)pcm[i] / 32768.0f;
    }

    uint8_t seen[200][10];   /* 2026-09-06: volle 77-Bit-Nutzlast statt CRC-14 (Kollision 1:16384 -> pro Slot ~3 %) */
    int num_seen = 0;
    int num_out = 0;

    if (mode == 1) {
        /* deep only */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                        out, max_out, seen, &num_seen, 0);
    } else if (mode == 2) {
        /* multi: standard then deep, accumulate */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, s_knob_std_tosr, 2, s_knob_std_ldpc,
                                        out, max_out, seen, &num_seen, 0);
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                            out, max_out, seen, &num_seen, num_out);
        }
    } else if (mode == 3) {
        int before;
        before = num_out;
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, s_knob_std_tosr, 2, s_knob_std_ldpc,
                                        out, max_out, seen, &num_seen, 0);
        s_pass_stats.pass_standard += (num_out - before);
        before = num_out;
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, s_knob_deep_fosr, s_knob_deep_ldpc,
                                            out, max_out, seen, &num_seen, num_out);
        }
        s_pass_stats.pass_deep += (num_out - before);

        /* Subtract-Runden: alles Neue der letzten Runde kohaerent abziehen,
         * dann standard + deep ueber das Residuum. JTDX faehrt 2-3 Runden;
         * eine Runde kostet auf dem Pi 4B ~0,8 s, Stufe 2 hat 12 s Budget.
         * Abbruch, sobald eine Runde nichts Neues bringt. */
        int round_start = 0;
        for (int round = 0; round < s_knob_sub_rounds && num_out < max_out; ++round) {
            int round_end = num_out;
            if (round_end <= round_start) break;
            for (int i = round_start; i < round_end; ++i) {
                if (out[i].score >= s_knob_sub_score) {
                    _ft8_subtract_decoded(signal, out[i].message, out[i].freq_hz, out[i].dt_s);
                }
            }
            round_start = round_end;
            before = num_out;
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, s_knob_std_tosr, 2, s_knob_std_ldpc,
                                            out, max_out, seen, &num_seen, num_out);
            if (num_out < max_out) {
                num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, s_knob_deep_fosr, s_knob_deep_ldpc,
                                                out, max_out, seen, &num_seen, num_out);
            }
            if (round == 0) s_pass_stats.pass_subtract_residual += (num_out - before);
            else            s_pass_stats.pass_subtract_round2 += (num_out - before);
        }

        before = num_out;
        if (num_out < max_out) {
            num_out = _ft8_hint_pass_signal(signal, FT8_SLOT_SAMPLES, s_knob_hint_osr, s_knob_hint_osr,
                                             out, max_out, seen, &num_seen, num_out);
        }
        s_pass_stats.pass_hint += (num_out - before);

        s_pass_stats.slots_decoded++;
    } else {
        /* mode 0 / default: standard */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, s_knob_std_tosr, 2, s_knob_std_ldpc,
                                        out, max_out, seen, &num_seen, 0);
    }

    free(signal);
    return num_out;
}


/* ========================================================================= *
 * FT4 protocol — same toolchain, different tone count / spacing / slot.
 *
 * FT4 is 4-FSK with 105 channel symbols, 0.048 s symbol period
 * (≈20.833 Hz tone spacing) and a 7.5 s slot. Decode goes through the
 * same monitor/wf/find-candidates pipeline as FT8 — we just set
 * monitor_config.protocol = FTX_PROTOCOL_FT4 and feed in a 7.5 s
 * buffer. TX synthesis needs FT4-specific constants but reuses the
 * same GFSK shaper.
 * ========================================================================= */
#define FT4_SLOT_SECONDS_X10  75                                 /* 7.5 * 10 */
#define FT4_SLOT_SAMPLES      ((FT8_SAMPLE_RATE_HZ * FT4_SLOT_SECONDS_X10) / 10)  /* 90000 */
#define FT4_TONE_SPACING_HZ   20.833333f                         /* 1 / 0.048 */
#define FT4_SYMBOL_PERIOD_S   0.048f
#define FT4_NUM_SYMBOLS_C     105                                /* avoid clash with FT4_NN macro */
#define FT4_SAMPLES_PER_SYM_C 576                                /* 12000 * 0.048 */
#define FT4_TX_SAMPLES        (FT4_NUM_SYMBOLS_C * FT4_SAMPLES_PER_SYM_C)  /* 60480 */
#define FT4_SYMBOL_BT         1.0f

extern void ft4_encode(const uint8_t *payload, uint8_t *tones);


/* v0.8.0 Build I: FT4 mode-aware Decoder. Wir machen es einfacher als
 * FT8 (kein Subtract weil 7.5s-Slot zu kurz fuer sinnvolle Residual-
 * Decodes). mode=0/standard: osr=2, LDPC=25; mode=1/deep+higher: osr=4,
 * LDPC=50. Adaptive LDPC-Factor wird auch hier respektiert. */
static int _ft4_decode_one_pass(
    int16_t const* pcm,
    int            time_osr,
    int            freq_osr,
    int            ldpc_iters,
    ft8_shim_result_t* out,
    int                max_out
) {
    float* signal = (float*)malloc(sizeof(float) * FT4_SLOT_SAMPLES);
    if (signal == NULL) return -1;
    for (int i = 0; i < FT4_SLOT_SAMPLES; ++i) signal[i] = (float)pcm[i] / 32768.0f;

    monitor_t mon;
    monitor_config_t cfg;
    cfg.f_min       = 200.0f;
    cfg.f_max       = 3000.0f;
    cfg.sample_rate = FT8_SAMPLE_RATE_HZ;
    cfg.time_osr    = time_osr;
    cfg.freq_osr    = freq_osr;
    cfg.protocol    = FTX_PROTOCOL_FT4; cfg.window_mode = 0;
    monitor_init(&mon, &cfg);
    for (int pos = 0; pos + mon.block_size <= FT4_SLOT_SAMPLES; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    free(signal);

    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(&mon.wf, s_knob_max_cand, candidates, FT8_SHIM_MIN_SCORE);
    int eff_ldpc = (ldpc_iters * s_ldpc_factor_pct) / 100;
    if (eff_ldpc < 5) eff_ldpc = 5;

    uint8_t seen[200][10];   /* 2026-09-06: volle 77-Bit-Nutzlast statt CRC-14 (Kollision 1:16384 -> pro Slot ~3 %) */
    int num_seen = 0, num_out = 0;
    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];
        ftx_message_t message; ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, cand, eff_ldpc, &message, &status)) continue;
        int dup = 0;
        for (int j = 0; j < num_seen; ++j) if (memcmp(seen[j], message.payload, 10) == 0) { dup = 1; break; }
        if (dup) continue;
        if (num_seen < 200) memcpy(seen[num_seen++], message.payload, 10);
        char text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t offsets;
        if (ftx_message_decode(&message, &s_hash_if, text, &offsets) != FTX_MESSAGE_RC_OK) continue;
        ft8_shim_result_t* r = &out[num_out++];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';
        r->snr_db_est = (cand->score / 2) - 24;
        r->score = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon.wf.time_osr) * mon.symbol_period
                   - _dt_window_corr_s(&mon) - FT4_DT_ORIGIN_S;
        r->freq_hz = (mon.min_bin + cand->freq_offset + (float)cand->freq_sub / mon.wf.freq_osr) / mon.symbol_period;
    }
    monitor_free(&mon);
    return num_out;
}


int ft4_shim_decode_slot_v2(
    const int16_t* pcm,
    int            n_samples,
    int            mode,
    ft8_shim_result_t* out,
    int            max_out
) {
    if (pcm == NULL || out == NULL || max_out <= 0) return -1;
    if (n_samples < FT4_SLOT_SAMPLES) return -1;
    if (mode == 1) {
        /* deep: osr=4 LDPC=50 */
        return _ft4_decode_one_pass(pcm, 4, 4, 50, out, max_out);
    } else if (mode == 2 || mode == 3) {
        /* multi/extreme: pass1 standard + dedupe-skip handled inside;
         * fuer FT4 nehmen wir nur deep weil 7.5s-Slot zu kurz fuer
         * sinnvolle Subtract-Loops. Trotzdem hoehere LDPC-Iter bringt
         * Mehrwert. */
        int n = _ft4_decode_one_pass(pcm, 2, 2, 25, out, max_out);
        if (n < 0) return n;
        /* nochmal mit deep, in zweite Haelfte (Dedupe per Pi-Code im
         * Picker — FT4 ist weniger congested, Dups selten) */
        if (n < max_out) {
            int n2 = _ft4_decode_one_pass(pcm, 4, 4, 50, out + n, max_out - n);
            if (n2 > 0) n += n2;
        }
        return n;
    }
    /* mode=0 standard */
    return _ft4_decode_one_pass(pcm, 2, 2, 25, out, max_out);
}


int ft4_shim_decode_slot(
    const int16_t* pcm,
    int            n_samples,
    ft8_shim_result_t* out,
    int            max_out
) {
    if (pcm == NULL || out == NULL || max_out <= 0) {
        return -1;
    }
    if (n_samples < FT4_SLOT_SAMPLES) {
        return -1;
    }

    float* signal = (float*)malloc(sizeof(float) * FT4_SLOT_SAMPLES);
    if (signal == NULL) {
        return -1;
    }
    for (int i = 0; i < FT4_SLOT_SAMPLES; ++i) {
        signal[i] = (float)pcm[i] / 32768.0f;
    }

    monitor_t mon;
    monitor_config_t cfg;
    cfg.f_min       = 200.0f;
    cfg.f_max       = 3000.0f;
    cfg.sample_rate = FT8_SAMPLE_RATE_HZ;
    cfg.time_osr    = 2;
    cfg.freq_osr    = 2;
    cfg.protocol    = FTX_PROTOCOL_FT4; cfg.window_mode = 0;
    monitor_init(&mon, &cfg);

    for (int pos = 0; pos + mon.block_size <= FT4_SLOT_SAMPLES; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    free(signal);

    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, s_knob_max_cand, candidates, FT8_SHIM_MIN_SCORE
    );

    uint8_t seen[200][10];   /* 2026-09-06: volle 77-Bit-Nutzlast statt CRC-14 (Kollision 1:16384 -> pro Slot ~3 %) */
    int      num_seen = 0;
    int      num_out  = 0;

    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];

        ftx_message_t       message;
        ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, cand, FT8_SHIM_LDPC_ITERS, &message, &status)) {
            continue;
        }

        int dup = 0;
        for (int j = 0; j < num_seen; ++j) {
            if (memcmp(seen[j], message.payload, 10) == 0) { dup = 1; break; }
        }
        if (dup) continue;
        if (num_seen < (int)(sizeof(seen) / sizeof(seen[0]))) {
            memcpy(seen[num_seen++], message.payload, 10);
        }

        char                   text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t  offsets;
        ftx_message_rc_t       rc = ftx_message_decode(&message, &s_hash_if, text, &offsets);
        if (rc != FTX_MESSAGE_RC_OK) continue;

        ft8_shim_result_t* r = &out[num_out];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';
        r->snr_db_est = (cand->score / 2) - 24;
        r->score      = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon.wf.time_osr)
                   * mon.symbol_period - _dt_window_corr_s(&mon) - FT8_DT_ORIGIN_S;
        r->freq_hz = (mon.min_bin
                      + cand->freq_offset
                      + (float)cand->freq_sub / mon.wf.freq_osr)
                     / mon.symbol_period;
        ++num_out;
    }

    monitor_free(&mon);
    return num_out;
}


/* FT4 GFSK synth — same shape as FT8 but with 4-FSK alphabet (values
 * 0..3 instead of 0..7), 105 symbols, 576 samples/symbol and tone
 * spacing of 1/0.048 Hz ≈ 20.833 Hz. */
static void _ft4_synth_gfsk(const uint8_t* tones, float f0, float* signal) {
    int n_spsym = FT4_SAMPLES_PER_SYM_C;
    int n_wave  = FT4_NUM_SYMBOLS_C * n_spsym;
    int n_total = n_wave + 2 * n_spsym;

    float dphi_peak = 2.0f * (float)M_PI * FT4_TONE_SPACING_HZ / FT8_SAMPLE_RATE_HZ;
    float* dphi = (float*)calloc((size_t)n_total, sizeof(float));
    if (dphi == NULL) return;
    for (int i = 0; i < n_total; ++i) {
        dphi[i] = 2.0f * (float)M_PI * f0 / FT8_SAMPLE_RATE_HZ;
    }

    float* pulse = (float*)malloc(sizeof(float) * 3 * n_spsym);
    if (pulse == NULL) { free(dphi); return; }
    _gfsk_pulse(n_spsym, FT4_SYMBOL_BT, pulse);

    for (int i = 0; i < FT4_NUM_SYMBOLS_C; ++i) {
        int ib = i * n_spsym;
        for (int j = 0; j < 3 * n_spsym; ++j) {
            dphi[j + ib] += dphi_peak * (float)tones[i] * pulse[j];
        }
    }
    for (int j = 0; j < 2 * n_spsym; ++j) {
        dphi[j]                                     = dphi[2 * n_spsym]
            - dphi_peak * (float)tones[0];
        dphi[j + FT4_NUM_SYMBOLS_C * n_spsym]       =
            dphi[FT4_NUM_SYMBOLS_C * n_spsym - 1] - dphi_peak * (float)tones[FT4_NUM_SYMBOLS_C - 1];
    }

    float phi = 0.0f;
    for (int k = 0; k < n_wave; ++k) {
        signal[k] = sinf(phi);
        phi      += dphi[k + n_spsym];
        if (phi >= 2.0f * (float)M_PI) phi -= 2.0f * (float)M_PI;
    }

    int n_ramp = n_spsym / 8;
    for (int i = 0; i < n_ramp; ++i) {
        float w = 0.5f * (1.0f - cosf((float)M_PI * (float)i / n_ramp));
        signal[i]              *= w;
        signal[n_wave - 1 - i] *= w;
    }

    free(pulse);
    free(dphi);
}


int ft4_shim_synth_message(
    const char* text,
    float       audio_freq_hz,
    float       amplitude,
    int16_t*    out_pcm,
    int         out_capacity
) {
    if (text == NULL || out_pcm == NULL || out_capacity < FT4_TX_SAMPLES) {
        return -1;
    }
    if (!(audio_freq_hz > 0.0f) || audio_freq_hz > 6000.0f) {
        return -1;
    }
    if (!(amplitude == amplitude)) return -1;
    if (amplitude <= 0.0f) amplitude = 0.9f;
    if (amplitude > 1.0f)  amplitude = 1.0f;

    ftx_message_t msg;
    /* v0.6.4: gleicher Bug-Fix wie ft8_shim_synth_message — hash-if
     * passieren damit compound-calls encoded werden koennen. */
    ftx_message_rc_t rc = ftx_message_encode(&msg, &s_hash_if, text);
    if (rc != FTX_MESSAGE_RC_OK) return -1;

    uint8_t tones[FT4_NUM_SYMBOLS_C];
    ft4_encode(msg.payload, tones);

    float* signal = (float*)malloc(sizeof(float) * FT4_TX_SAMPLES);
    if (signal == NULL) return -1;
    _ft4_synth_gfsk(tones, audio_freq_hz, signal);

    for (int i = 0; i < FT4_TX_SAMPLES; ++i) {
        float s = signal[i] * amplitude * 32767.0f;
        if (s > 32767.0f) s = 32767.0f;
        if (s < -32768.0f) s = -32768.0f;
        out_pcm[i] = (int16_t)s;
    }
    free(signal);
    return FT4_TX_SAMPLES;
}


int ft8_shim_synth_message(
    const char* text,
    float       audio_freq_hz,
    float       amplitude,
    int16_t*    out_pcm,
    int         out_capacity
) {
    if (text == NULL || out_pcm == NULL || out_capacity < FT8_TX_SAMPLES) {
        return -1;
    }
    /* Reject NaN, Inf, negative, and anything past the practical FT8
     * audio passband (200..3000 Hz, but allow a margin). Without this
     * guard, NaN propagates through the phase accumulator and produces
     * garbage TX audio.  */
    if (!(audio_freq_hz > 0.0f) || audio_freq_hz > 6000.0f) {
        return -1;
    }
    if (!(amplitude == amplitude)) {  /* NaN check */
        return -1;
    }
    if (amplitude <= 0.0f) amplitude = 0.9f;
    if (amplitude > 1.0f)  amplitude = 1.0f;

    ftx_message_t msg;
    /* v0.6.4 Bug-Fix: hash-table-Interface passieren statt NULL.
     * Sonst kann ftx_message_encode kein compound/hashed callsign
     * "<RT25KR>" encoden — der Encoder muss save_callsign aufrufen
     * koennen um den 22-bit Hash in die ftx_message_t zu schreiben.
     * Vorher: synth failed beim QSO mit /P, /MM oder Sonderrufzeichen
     * Stations, das QSO blieb bei R-Report haengen. */
    ftx_message_rc_t rc = ftx_message_encode(&msg, &s_hash_if, text);
    if (rc != FTX_MESSAGE_RC_OK) {
        return -1;
    }

    uint8_t tones[FT8_NUM_SYMBOLS];
    ft8_encode(msg.payload, tones);

    float* signal = (float*)malloc(sizeof(float) * FT8_TX_SAMPLES);
    if (signal == NULL) return -1;
    _synth_gfsk(tones, audio_freq_hz, signal);

    for (int i = 0; i < FT8_TX_SAMPLES; ++i) {
        float s = signal[i] * amplitude * 32767.0f;
        if (s > 32767.0f) s = 32767.0f;
        if (s < -32768.0f) s = -32768.0f;
        out_pcm[i] = (int16_t)s;
    }
    free(signal);
    return FT8_TX_SAMPLES;
}
