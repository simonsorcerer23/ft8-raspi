// Reactive state stores using Svelte 5 runes.
import { api } from './api.js';

// --------------------------------------------------------------- Status
function makeStatusStore() {
  let value = $state({
    callsign: null,
    state: 'UNKNOWN',
    last_lock_reason: null,
    cq_count: 0,
    auto_answer: false,
    current_qso_call: null,
    rig: { freq_hz: null, mode: null, ptt: null, swr: null },
    gps: { mode: 0, lat: null, lon: null, sats_seen: 0, sats_used: 0 },
  });
  let lastFetch = $state(0);
  let error = $state(null);

  async function refresh() {
    try { value = await api.status(); lastFetch = Date.now(); error = null; }
    catch (e) { error = e.message; }
  }
  return { get value() { return value; }, get error(){ return error; },
           get lastFetch(){ return lastFetch; }, refresh };
}

// --------------------------------------------------------------- Healthcheck
function makeHealthStore() {
  let value = $state({ overall: 'yellow', sections: {} });
  let error = $state(null);
  async function refresh() {
    try { value = await api.healthcheck(); error = null; }
    catch (e) { error = e.message; }
  }
  return { get value() { return value; }, get error(){ return error; }, refresh };
}

// --------------------------------------------------------------- Decodes (live)
function makeDecodeStore(cap = 100) {
  let items = $state([]);
  function push(decode) { items = [decode, ...items].slice(0, cap); }
  function clear() { items = []; }
  function setAll(list) { items = list; }
  return { get items() { return items; }, push, clear, setAll };
}

// --------------------------------------------------------------- Log (paginated + sortable)
function makeLogStore() {
  let qsos = $state([]);
  let total = $state(0);
  let page = $state(1);
  let pageSize = $state(50);
  let filters = $state({
    call: '', prefix: '', band: '', grid: '',
    since_days: null, min_snr: null,
  });
  let sortBy = $state('qso_start');
  let sortDir = $state('desc');
  let loading = $state(false);

  async function refresh() {
    loading = true;
    try {
      const r = await api.log({
        page, page_size: pageSize,
        call_filter: filters.call || undefined,
        prefix: filters.prefix || undefined,
        band: filters.band || undefined,
        grid_filter: filters.grid || undefined,
        since_days: filters.since_days || undefined,
        min_snr_rcvd: filters.min_snr || undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
      });
      qsos = r.qsos;
      total = r.total;
    } finally { loading = false; }
  }
  return {
    get qsos()      { return qsos; },
    get total()     { return total; },
    get page()      { return page; },
    get pageSize()  { return pageSize; },
    get filters()   { return filters; },
    get sortBy()    { return sortBy; },
    get sortDir()   { return sortDir; },
    get loading()   { return loading; },
    setPage(p)      { page = p; refresh(); },
    setFilter(k, v) { filters[k] = v; page = 1; refresh(); },
    clearFilters()  { filters = { call:'', prefix:'', band:'', grid:'',
                                   since_days:null, min_snr:null };
                      page = 1; refresh(); },
    setSort(col) {
      // toggle dir if same col; otherwise switch with default desc
      if (sortBy === col) { sortDir = sortDir === 'desc' ? 'asc' : 'desc'; }
      else { sortBy = col; sortDir = col === 'qso_start' ? 'desc' : 'asc'; }
      refresh();
    },
    refresh,
  };
}

// --------------------------------------------------------------- Map
function makeMapStore() {
  let mode = $state('all');           // 'all' | 'worked' | 'heard'
  let minutesHeard = $state(60);
  let data = $state({ operator_lat: null, operator_lon: null, markers: [] });
  let loading = $state(false);

  async function refresh() {
    loading = true;
    try { data = await api.map({ mode, minutes_heard: minutesHeard }); }
    finally { loading = false; }
  }
  return {
    get mode() { return mode; },
    get minutesHeard() { return minutesHeard; },
    get data() { return data; },
    get loading() { return loading; },
    setMode(m) { mode = m; refresh(); },
    setMinutesHeard(n) { minutesHeard = n; refresh(); },
    refresh,
  };
}

export const statusStore  = makeStatusStore();
export const healthStore  = makeHealthStore();
export const decodeStore  = makeDecodeStore();
export const logStore     = makeLogStore();
export const mapStore     = makeMapStore();
