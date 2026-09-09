/* shim_harness.c — Decoder ohne Python, fuer Sanitizer-Laeufe.
 *
 * 2026-09-09, Multicore-Umbau: Der cffi-Weg ueber Python taugt nicht fuer
 * ThreadSanitizer/AddressSanitizer (der Interpreter ist nicht instrumentiert,
 * LD_PRELOAD-Tricks sind wackelig). Dieses Programm liest rohe int16-PCM-
 * Slots (12 kHz mono, 180000 Samples FT8) und ruft dieselben Shim-Funktionen
 * auf wie die Appliance. Die Ausgabe (eine Zeile je Decode) ist gegen den
 * Python-Goldstandard vergleichbar.
 *
 * Bauen (aus backend/ft8_appliance/decode, nachdem libft8.a und die
 * kiss_fft-Objekte gebaut sind):
 *
 *   gcc -O1 -g -fopenmp -fsanitize=thread  -I../../../vendor/ft8_lib \
 *       ft8_shim.c shim_harness.c ../../../vendor/ft8_lib/libft8.a \
 *       ../../../vendor/ft8_lib/.build/fft/kiss_fft.o \
 *       ../../../vendor/ft8_lib/.build/fft/kiss_fftr.o -lm -o /tmp/shim_tsan
 *
 *   /tmp/shim_tsan <modus 0..3> <slot.raw> [weitere .raw ...]
 *
 * Vor jedem Slot wird die Hashtabelle wie im Benchmark mit den Rufzeichen
 * aus <slot>.calls (eines je Zeile, optional) gefuellt.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char  message[40];
    int   snr_db_est;
    float dt_s;
    float freq_hz;
    int   score;
} ft8_shim_result_t;

int ft8_shim_decode_slot(const int16_t* pcm, int n_samples, ft8_shim_result_t* out, int max_out);
int ft8_shim_decode_slot_v2(const int16_t* pcm, int n_samples, int mode, ft8_shim_result_t* out, int max_out);
int ft8_shim_hash_table_save(const char* callsign, uint32_t n22);
int ft8_shim_set_knob(const char* name, int value);
void ft8_shim_set_osd_depth(int depth);
void ft8_shim_set_ldpc_factor(int pct);
int ft8_shim_omp_max_threads(void);

#define SLOT 180000

static void load_calls(const char* rawpath) {
    char p[4096];
    snprintf(p, sizeof p, "%s.calls", rawpath);
    FILE* f = fopen(p, "r");
    if (!f) return;
    char line[64];
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (line[0]) ft8_shim_hash_table_save(line, 0);
    }
    fclose(f);
}

int main(int argc, char** argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <mode 0..3> <slot.raw> ...\n", argv[0]); return 2; }
    int mode = atoi(argv[1]);
    ft8_shim_set_osd_depth(2);
    ft8_shim_set_ldpc_factor(150);
    const char* thr = getenv("SHIM_THREADS");
    if (thr) ft8_shim_set_knob("threads", atoi(thr));
    fprintf(stderr, "threads=%d mode=%d\n", ft8_shim_omp_max_threads(), mode);

    static int16_t pcm[SLOT];
    static ft8_shim_result_t out[50];
    for (int a = 2; a < argc; ++a) {
        FILE* f = fopen(argv[a], "rb");
        if (!f) { perror(argv[a]); return 1; }
        memset(pcm, 0, sizeof pcm);
        size_t n = fread(pcm, sizeof(int16_t), SLOT, f);
        fclose(f);
        if (n == 0) { fprintf(stderr, "%s: leer\n", argv[a]); return 1; }
        load_calls(argv[a]);
        /* Warmlauf wie im Goldstandard: erst der Standard-Pass, dann der Modus. */
        ft8_shim_decode_slot(pcm, SLOT, out, 50);
        int k = (mode == 0) ? ft8_shim_decode_slot(pcm, SLOT, out, 50)
                            : ft8_shim_decode_slot_v2(pcm, SLOT, mode, out, 50);
        if (k < 0) { fprintf(stderr, "%s: decode fehlgeschlagen\n", argv[a]); return 1; }
        for (int i = 0; i < k; ++i)
            printf("%s\t%d\t%.6f\t%.3f\t%d\t%s\n", argv[a], out[i].snr_db_est, out[i].dt_s, out[i].freq_hz, out[i].score, out[i].message);
    }
    return 0;
}
