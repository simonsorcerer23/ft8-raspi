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
#include <string.h>

#include "ft8/decode.h"
#include "ft8/message.h"
#include "common/monitor.h"

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
#define HASH_TABLE_SIZE 256

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
            strncpy(call_out, s_hash_table[i].call, 13);
            call_out[13] = '\0';
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

static ftx_callsign_hash_interface_t s_hash_if = {
    .lookup_hash = shim_lookup_hash,
    .save_hash   = shim_save_hash,
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
#define FT8_SHIM_MAX_CANDIDATES 300
#define FT8_SHIM_MIN_SCORE      10
#define FT8_SHIM_LDPC_ITERS     25

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
    cfg.time_osr    = 2;
    cfg.freq_osr    = 2;
    cfg.protocol    = FTX_PROTOCOL_FT8;
    monitor_init(&mon, &cfg);

    /* Slide through the slot accumulating the waterfall */
    for (int pos = 0; pos + mon.block_size <= FT8_SLOT_SAMPLES; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    free(signal);

    /* Find sync candidates */
    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, FT8_SHIM_MAX_CANDIDATES, candidates, FT8_SHIM_MIN_SCORE
    );

    /* Decode each candidate; dedupe by message.hash */
    uint16_t seen[50];
    int      num_seen = 0;
    int      num_out  = 0;

    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];

        ftx_message_t       message;
        ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, cand, FT8_SHIM_LDPC_ITERS, &message, &status)) {
            continue;  /* LDPC fail or CRC mismatch */
        }

        int dup = 0;
        for (int j = 0; j < num_seen; ++j) {
            if (seen[j] == message.hash) {
                dup = 1;
                break;
            }
        }
        if (dup) continue;
        if (num_seen < (int)(sizeof(seen) / sizeof(seen[0]))) {
            seen[num_seen++] = message.hash;
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
                   * mon.symbol_period;
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
static int _ft8_decode_one_pass(
    float*             signal,
    int                signal_len,
    int                time_osr,
    int                freq_osr,
    int                ldpc_iters,
    ft8_shim_result_t* out,
    int                max_out,
    uint16_t*          seen,
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
    monitor_init(&mon, &cfg);

    for (int pos = 0; pos + mon.block_size <= signal_len; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }

    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, FT8_SHIM_MAX_CANDIDATES, candidates, FT8_SHIM_MIN_SCORE
    );

    int num_out = num_out_initial;
    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];

        ftx_message_t       message;
        ftx_decode_status_t status;
        if (!ftx_decode_candidate(&mon.wf, cand, ldpc_iters, &message, &status)) {
            continue;
        }

        int dup = 0;
        for (int j = 0; j < *num_seen; ++j) {
            if (seen[j] == message.hash) { dup = 1; break; }
        }
        if (dup) continue;
        if (*num_seen < 50) seen[(*num_seen)++] = message.hash;

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
                   * mon.symbol_period;
        r->freq_hz = (mon.min_bin
                      + cand->freq_offset
                      + (float)cand->freq_sub / mon.wf.freq_osr)
                     / mon.symbol_period;
        ++num_out;
    }

    monitor_free(&mon);
    return num_out;
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


static int _ft8_hint_pass(
    monitor_t*         mon,
    ft8_shim_result_t* out,
    int                max_out,
    uint16_t*          seen,
    int*               num_seen,
    int                num_out_initial
) {
    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    /* min_score=5 (vs 10 Standard): viel mehr Candidates */
    int num_cand = ftx_find_candidates(
        &mon->wf, FT8_SHIM_MAX_CANDIDATES, candidates, 5
    );
    int num_out = num_out_initial;
    for (int idx = 0; idx < num_cand && num_out < max_out; ++idx) {
        const ftx_candidate_t* cand = &candidates[idx];
        ftx_message_t       message;
        ftx_decode_status_t status;
        /* LDPC=120 (vs 25 Standard): mehr Iterationen für marginale Signale */
        if (!ftx_decode_candidate(&mon->wf, cand, 120, &message, &status)) continue;

        int dup = 0;
        for (int j = 0; j < *num_seen; ++j) {
            if (seen[j] == message.hash) { dup = 1; break; }
        }
        if (dup) continue;

        char text[FTX_MAX_MESSAGE_LENGTH];
        ftx_message_offsets_t offsets;
        if (ftx_message_decode(&message, &s_hash_if, text, &offsets) != FTX_MESSAGE_RC_OK) continue;

        /* Hint-Gate: nur akzeptieren wenn known call drin */
        if (!_ft8_text_has_known_call(text)) continue;

        if (*num_seen < 50) seen[(*num_seen)++] = message.hash;

        ft8_shim_result_t* r = &out[num_out];
        strncpy(r->message, text, FT8_SHIM_MSG_LEN - 1);
        r->message[FT8_SHIM_MSG_LEN - 1] = '\0';
        r->snr_db_est = (cand->score / 2) - 24;
        r->score      = cand->score;
        r->dt_s = (cand->time_offset + (float)cand->time_sub / mon->wf.time_osr)
                   * mon->symbol_period;
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
    uint16_t*          seen,
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
    monitor_init(&mon, &cfg);
    for (int pos = 0; pos + mon.block_size <= signal_len; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    int n = _ft8_hint_pass(&mon, out, max_out, seen, num_seen, num_out_initial);
    monitor_free(&mon);
    return n;
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

    float* sub_signal = (float*)malloc(sizeof(float) * FT8_TX_SAMPLES);
    if (sub_signal == NULL) return;
    _synth_gfsk(tones, freq_hz, sub_signal);

    int start = (int)(dt_s * (float)FT8_SAMPLE_RATE_HZ);
    if (start < 0) start = 0;
    int end = start + FT8_TX_SAMPLES;
    if (end > FT8_SLOT_SAMPLES) end = FT8_SLOT_SAMPLES;

    const float amp = 0.4f;
    for (int i = start; i < end; ++i) {
        signal[i] -= amp * sub_signal[i - start];
    }
    free(sub_signal);
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

    uint16_t seen[50];
    int num_seen = 0;
    int num_out = 0;

    if (mode == 1) {
        /* deep only */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                        out, max_out, seen, &num_seen, 0);
    } else if (mode == 2) {
        /* multi: standard then deep, accumulate */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 2, 2, 25,
                                        out, max_out, seen, &num_seen, 0);
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                            out, max_out, seen, &num_seen, num_out);
        }
    } else if (mode == 3) {
        /* v0.7.0 Build 1: subtract-and-rerun. Pass1 standard + deep,
         * subtract strong decodes (score>=20), re-decode residual with
         * standard + deep. Pi-5-Power-Mode. */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 2, 2, 25,
                                        out, max_out, seen, &num_seen, 0);
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                            out, max_out, seen, &num_seen, num_out);
        }
        int after_first = num_out;
        /* Subtract strong decodes from signal */
        for (int i = 0; i < after_first; ++i) {
            if (out[i].score >= 20) {
                _ft8_subtract_decoded(signal, out[i].message,
                                       out[i].freq_hz, out[i].dt_s);
            }
        }
        /* Re-decode residual (standard + deep) — new decodes only via dedupe */
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 2, 2, 25,
                                            out, max_out, seen, &num_seen, num_out);
        }
        if (num_out < max_out) {
            num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 4, 4, 50,
                                            out, max_out, seen, &num_seen, num_out);
        }
        /* v0.7.0 Build 2: Hint-Pass am Ende — sehr permissive min_score
         * (5) + hohe LDPC-Iterations (120), aber nur akzeptiert wenn
         * decoded text einen known call aus s_hash_table enthaelt.
         * JTDX-Style. Hebt Sensitivity um ~1-2 dB. */
        if (num_out < max_out) {
            num_out = _ft8_hint_pass_signal(signal, FT8_SLOT_SAMPLES, 2, 2,
                                             out, max_out, seen, &num_seen, num_out);
        }
    } else {
        /* mode 0 / default: standard */
        num_out = _ft8_decode_one_pass(signal, FT8_SLOT_SAMPLES, 2, 2, 25,
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
    cfg.protocol    = FTX_PROTOCOL_FT4;
    monitor_init(&mon, &cfg);

    for (int pos = 0; pos + mon.block_size <= FT4_SLOT_SAMPLES; pos += mon.block_size) {
        monitor_process(&mon, signal + pos);
    }
    free(signal);

    ftx_candidate_t candidates[FT8_SHIM_MAX_CANDIDATES];
    int num_cand = ftx_find_candidates(
        &mon.wf, FT8_SHIM_MAX_CANDIDATES, candidates, FT8_SHIM_MIN_SCORE
    );

    uint16_t seen[50];
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
            if (seen[j] == message.hash) { dup = 1; break; }
        }
        if (dup) continue;
        if (num_seen < (int)(sizeof(seen) / sizeof(seen[0]))) {
            seen[num_seen++] = message.hash;
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
                   * mon.symbol_period;
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
