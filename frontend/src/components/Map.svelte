<script>
  import { onMount, onDestroy } from 'svelte';
  import L from 'leaflet';
  import 'leaflet/dist/leaflet.css';
  import { api } from '../lib/api.js';
  import { mapStore, decodeStore, statusStore } from '../lib/stores.svelte.js';
  import { utcMillis, fmtUtcTime, fmtUtcDateTime, parseUtc } from '../lib/time.js';
  import { greatCircle, gridToLatLon } from '../lib/geo.js';

  let el;
  let map;
  let stationLayer, workedArcLayer, liveArcLayer, dxLayer, locLayer, heatLayer, gridLayer, terminatorLayer, coverageLayer;
  let operatorMarker;

  // Layer-toggle state
  let showStations = $state(true);
  let showLiveArcs = $state(true);        // Großkreise zu gerade dekodierten Stationen
  let showHeatmap  = $state(true);
  let showDxCluster = $state(true);
  let showOperatingLocations = $state(true);
  let showGrid = $state(true);            // Maidenhead-Locator-Raster
  let showTerminator = $state(true);      // Day/Night-Terminator (Gray-Line)
  let showCoverage = $state(false);        // Coverage-Envelope-Polygon aus PSK-Reporter
  let coverageHours = $state(24);          // Time-Window für Coverage-Aggregat
  let coverageBand = $state('');           // '' = alle Bänder; sonst '20m' etc.

  const COLOURS = {
    worked: '#22c55e', heard: '#f59e0b', both: '#38bdf8',
    operator: '#ec4899', dx: '#a78bfa', loc: '#fb923c',
    live: '#facc15',
  };

  function makeDot(colour, size = 14) {
    return L.divIcon({
      className: 'ft8-marker',
      html: `<span style="background:${colour}; width:${size}px; height:${size}px"></span>`,
      iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    });
  }

  function drawGreatCircle(layer, p1, p2, style) {
    // Split at antimeridian → may yield 1 or 2 segments
    for (const seg of greatCircle(p1, p2, 80)) {
      L.polyline(seg, style).addTo(layer);
    }
  }

  function renderStations() {
    stationLayer.clearLayers();
    workedArcLayer.clearLayers();
    const d = mapStore.data;

    if (operatorMarker) operatorMarker.remove();
    if (d.operator_lat != null && d.operator_lon != null) {
      operatorMarker = L.marker([d.operator_lat, d.operator_lon], {
        icon: L.divIcon({
          className: 'ft8-operator',
          html: '<span></span>',
          iconSize: [18, 18], iconAnchor: [9, 9],
        }),
      }).bindPopup(
        `<strong>Du</strong><br>${d.operator_lat.toFixed(3)}, ${d.operator_lon.toFixed(3)}`
      ).addTo(map);
    }

    if (!showStations) return;

    const op = (d.operator_lat != null && d.operator_lon != null)
      ? [d.operator_lat, d.operator_lon] : null;

    for (const m of d.markers) {
      const marker = L.marker([m.lat, m.lon], { icon: makeDot(COLOURS[m.kind]) });
      marker.bindPopup(buildStationPopup(m));
      marker.addTo(stationLayer);
      // Worked + both: subtle green great-circle to operator
      if (m.kind !== 'heard' && op) {
        drawGreatCircle(workedArcLayer, op, [m.lat, m.lon], {
          color: COLOURS.worked, weight: 1, opacity: 0.2, dashArray: '4, 5',
        });
      }
    }
  }

  /**
   * Live great-circles to *currently decoded* stations — the bright arcs
   * that JTDX/WSJT-X/GridTracker users expect. Brightness fades with the
   * decode age so the freshest signals stand out.
   */
  function renderLiveArcs() {
    liveArcLayer.clearLayers();
    if (!showLiveArcs) return;

    // Operator location: GPS-Fix when available, sonst die vom Backend
    // gerechnete Locator-Mitte (default_locator-Fallback). Vorher fiel
    // die Anzeige indoor unter den Tisch, weil GPS-lat/lon null waren —
    // Sebastian sah keine Live-Großkreise obwohl der Pin auf der Karte
    // korrekt in JN58 stand.
    const opLat = statusStore.value.gps?.lat ?? mapStore.data.operator_lat;
    const opLon = statusStore.value.gps?.lon ?? mapStore.data.operator_lon;
    if (opLat == null || opLon == null) return;
    const op = [opLat, opLon];

    const now = Date.now();
    // Use whatever decodes the SSE/poll stream has — drawn youngest-first
    const items = decodeStore.items;
    const seen = new Set();
    for (const d of items) {
      if (!d.grid || !d.call_from) continue;
      if (seen.has(d.call_from)) continue;  // one arc per call
      seen.add(d.call_from);
      const target = gridToLatLon(d.grid);
      if (!target) continue;
      const age_s = (now - utcMillis(d.ts)) / 1000;
      // Fade 1.0 → 0.4 über 5 min; vorher 0.85 → 0.1 war zu schwach,
      // bei vielen frischen Decodes verlor man die Sicht auf einzelne
      // Bögen. Mindest-Opacity 0.4 hält ältere Spots noch erkennbar.
      const opacity = Math.max(0.4, 1.0 - age_s / 300 * 0.6);
      const colour = d.worked_before ? COLOURS.both : COLOURS.live;
      drawGreatCircle(liveArcLayer, op, target, {
        color: colour, weight: 3, opacity,
      });
      // Target dot prominenter
      L.circleMarker(target, {
        radius: 5, color: colour, weight: 2,
        fillColor: colour, fillOpacity: opacity,
      }).bindPopup(
        `<strong>${d.call_from}</strong><br>` +
        `${d.message}<br>` +
        `${d.grid} · ${d.snr_db ?? '?'} dB · ${d.band ?? ''}<br>` +
        `<small>${fmtUtcTime(d.ts)} UTC</small>`
      ).addTo(liveArcLayer);
    }
  }

  function buildStationPopup(m) {
    const lines = [`<strong>${m.call}</strong>`, `Grid: ${m.grid}`];
    if (m.band) lines.push(`Band: ${m.band}`);
    if (m.snr_best != null) lines.push(`Best SNR: ${m.snr_best} dB`);
    if (m.count > 1) lines.push(`Heard: ${m.count}×`);
    if (m.last_worked) lines.push(`Worked: ${fmtUtcDateTime(m.last_worked)}`);
    if (m.last_seen)   lines.push(`Seen:   ${fmtUtcDateTime(m.last_seen)}`);
    return lines.join('<br>');
  }

  async function refreshDx() {
    dxLayer.clearLayers();
    if (!showDxCluster) return;
    try {
      const r = await api.dxCluster({ minutes: 30 });
      if (!r.enabled || r.spots.length === 0) return;
      // Backend reichert mit cty.dat-Country-Center (lat/lon) an. Wir
      // jittern minimal damit mehrere Spots aus demselben Land sich
      // nicht exakt überlagern — sonst sieht's aus als wär nur einer da.
      const seenAtPoint = new Map();
      for (const sp of r.spots) {
        if (sp.lat == null || sp.lon == null) continue;
        const key = `${sp.lat.toFixed(1)},${sp.lon.toFixed(1)}`;
        const idx = seenAtPoint.get(key) ?? 0;
        seenAtPoint.set(key, idx + 1);
        const jitter = idx * 0.35;
        const lat = sp.lat + (idx % 2 === 0 ? jitter : -jitter);
        const lon = sp.lon + (idx > 1 ? jitter : -jitter);
        const m = L.marker([lat, lon], { icon: makeDot(COLOURS.dx, 10) });
        m.bindPopup(
          `<strong>${sp.spotted}</strong> · ${sp.band ?? ''}<br>` +
          `<small>${sp.country ?? '?'} (${sp.continent ?? '?'})</small><br>` +
          `${(sp.freq_hz / 1000).toFixed(1)} kHz<br>` +
          `<em>${sp.comment ?? ''}</em><br>` +
          `<small>${sp.spotter} · ${fmtUtcTime(sp.ts)} UTC</small>`
        );
        m.addTo(dxLayer);
      }
    } catch { /* ignore */ }
  }

  async function refreshLocations() {
    locLayer.clearLayers();
    if (!showOperatingLocations) return;
    try {
      const r = await api.operatingLocations();
      for (const loc of r.locations) {
        const marker = L.marker([loc.lat, loc.lon], { icon: makeDot(COLOURS.loc, 18) });
        marker.bindPopup(
          `<strong>📍 Operating location</strong><br>` +
          `${loc.qso_count} QSOs<br>` +
          `Bänder: ${loc.bands.join(', ')}<br>` +
          `Zeit: ${fmtUtcDateTime(loc.first_qso).slice(0,10)} – ${fmtUtcDateTime(loc.last_qso).slice(0,10)}`
        );
        marker.addTo(locLayer);
      }
    } catch { /* ignore */ }
  }

  async function refreshHeatmap() {
    heatLayer.clearLayers();
    if (!showHeatmap) return;
    try {
      const r = await api.heatmap({ minutes: 360 });
      // Simple density: stack semi-transparent circles, no leaflet.heat dep
      for (const p of r.points) {
        const radius = 30000 + 80000 * Math.min(1, p.weight);
        L.circle([p.lat, p.lon], {
          radius, color: COLOURS.heard, weight: 0,
          fillColor: COLOURS.heard, fillOpacity: 0.06 + 0.12 * Math.min(1, p.weight),
        }).addTo(heatLayer);
      }
    } catch { /* ignore */ }
  }

  onMount(() => {
    map = L.map(el, { zoomControl: true, attributionControl: false }).setView([30, 0], 2);

    L.tileLayer('/tiles/{z}/{x}/{y}.png', {
      maxZoom: 10, minZoom: 1, errorTileUrl: '',
    }).addTo(map);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 10, minZoom: 1, opacity: 0.7,
    }).addTo(map);

    stationLayer = L.layerGroup().addTo(map);
    workedArcLayer = L.layerGroup().addTo(map);
    liveArcLayer = L.layerGroup().addTo(map);
    dxLayer = L.layerGroup().addTo(map);
    locLayer = L.layerGroup().addTo(map);
    heatLayer = L.layerGroup().addTo(map);
    gridLayer = L.layerGroup();
    terminatorLayer = L.layerGroup();
    coverageLayer = L.layerGroup();
    map.on('zoomend moveend', renderGrid);

    mapStore.refresh();
    refreshLocations();
    refreshHeatmap();
    refreshDx();
    renderGrid();
    renderTerminator();
    // HF-Propagation folgt der Tageslicht-Grenze — alle 5 min reicht.
    setInterval(renderTerminator, 5 * 60 * 1000);
    const t = setInterval(() => {
      mapStore.refresh();
      refreshDx();
      refreshHeatmap();
    }, 30_000);
    return () => clearInterval(t);
  });

  onDestroy(() => { map?.remove(); });

  $effect(() => { mapStore.data; renderStations(); });
  $effect(() => { showStations; renderStations(); });
  $effect(() => { showLiveArcs; decodeStore.items; statusStore.value.gps; renderLiveArcs(); });
  $effect(() => { showHeatmap; refreshHeatmap(); });
  $effect(() => { showDxCluster; refreshDx(); });
  $effect(() => { showOperatingLocations; refreshLocations(); });
  $effect(() => {
    if (!gridLayer) return;
    if (showGrid) gridLayer.addTo(map);
    else gridLayer.remove();
    renderGrid();
  });
  $effect(() => {
    if (!terminatorLayer) return;
    if (showTerminator) terminatorLayer.addTo(map);
    else terminatorLayer.remove();
    renderTerminator();
  });
  $effect(() => {
    if (!coverageLayer) return;
    if (showCoverage) coverageLayer.addTo(map);
    else coverageLayer.remove();
    // Refresh wenn Toggle/Hours/Band sich ändert. $effect tracked die
    // Reads automatisch durch das Tupel-Abtasten unten.
    void showCoverage; void coverageHours; void coverageBand;
    refreshCoverage();
  });

  /**
   * Day/Night-Terminator (Gray-Line). FT8-relevant weil HF-Propagation
   * den Verlauf der Grenze folgt: graue Linie = beste DX-Bedingungen.
   *
   * Vereinfachte Berechnung (Genauigkeit <1° reicht für die Anzeige):
   *  1. Sonnen-Deklination δ aus dem Tag im Jahr
   *  2. Subsolare Länge aus UTC-Zeit
   *  3. Für jede Länge: Terminator-Breite via tan(lat) = -cos(HA)/tan(δ)
   *  4. Polygon = Terminator-Linie + Schließung am winterlichen Pol
   */
  function renderTerminator() {
    if (!terminatorLayer) return;
    terminatorLayer.clearLayers();
    if (!showTerminator) return;

    const now = new Date();
    const startOfYear = Date.UTC(now.getUTCFullYear(), 0, 1);
    const dayFrac = (now.getTime() - startOfYear) / 86_400_000;
    const utcHours = now.getUTCHours() + now.getUTCMinutes() / 60;

    const declRad = 23.45 * Math.PI / 180 * Math.sin(2 * Math.PI * (dayFrac - 81) / 365);
    const subsolarLon = -15 * (utcHours - 12);  // Längengrad direkt unter der Sonne

    // Punkte der Terminator-Kurve, Lon -180 → +180 in 2°-Schritten.
    const line = [];
    for (let lon = -180; lon <= 180; lon += 2) {
      const ha = (lon - subsolarLon) * Math.PI / 180;
      const tanDecl = Math.tan(declRad);
      // Schutz vor Equinox (tan≈0): dann fällt die Grenze auf den Äquator
      let lat = Math.abs(tanDecl) < 1e-6
              ? 0
              : Math.atan(-Math.cos(ha) / tanDecl) * 180 / Math.PI;
      // Math.atan liefert nur (-90,90) — passt direkt
      line.push([lat, lon]);
    }
    // Linie selbst (graue Kante)
    L.polyline(line, {
      color: '#fbbf24', weight: 1.5, opacity: 0.7, dashArray: '6,4',
    }).addTo(terminatorLayer);

    // Nacht-Polygon: alle Linienpunkte plus die "abgewandte" Polkappe.
    // Auf der Nordhalbkugel (δ > 0) ist die Nacht-Seite südlich der
    // Grenze nur in der Winterhälfte des Globus — die Polkappe gehört
    // zum Pol, dessen Sonne *nicht* steht. Heuristik: wenn δ > 0
    // → Nordpol ist hell, Südpol dunkel → Polygon schließt im S.
    const polePol = declRad > 0 ? -90 : 90;
    const poly = [
      [polePol, -180], ...line, [polePol, 180]
    ];
    L.polygon(poly, {
      color: '#1e293b', weight: 0,
      fillColor: '#0f172a', fillOpacity: 0.35,
    }).addTo(terminatorLayer);

    // Subsolar-Punkt als kleines ☀️-Symbol
    L.marker([declRad * 180 / Math.PI, subsolarLon], {
      icon: L.divIcon({
        className: 'ft8-subsolar',
        html: '<span>☀</span>',
        iconSize: [20, 20], iconAnchor: [10, 10],
      }),
      interactive: false,
    }).addTo(terminatorLayer);
  }

  // Coverage-Envelope: Polygon entlang der äußersten Reception-Reports
  // pro Azimut-Bin. Daten aus /api/stats/coverage-envelope (PSK-Reporter).
  // Färbt sich nach Recency: frische Bins (≤6h) leuchtend grün, ältere blass.
  async function refreshCoverage() {
    if (!coverageLayer) return;
    coverageLayer.clearLayers();
    if (!showCoverage) return;
    let data;
    try {
      const qs = new URLSearchParams({ hours: String(coverageHours) });
      if (coverageBand) qs.set('band', coverageBand);
      const r = await fetch(`/api/stats/coverage-envelope?${qs}`);
      data = await r.json();
    } catch { return; }
    if (!data?.bins?.length || data.home_lat == null) return;

    // Polygon-Vertices als [lat, lon] in Azimut-Reihenfolge. Backend
    // sortiert schon, hier nur 1:1 mappen + schließen indem wir das
    // erste Element nochmal anhängen.
    const pts = data.bins.map(b => [b.far_lat, b.far_lon]);
    if (pts.length < 3) return;
    pts.push(pts[0]);

    // Recency-Farbe: jüngster Bin im Set entscheidet die Hauptfarbe.
    const minAge = Math.min(...data.bins.map(b => b.latest_age_h));
    let color;
    if (minAge < 1)  color = '#22c55e';       // frisch — leuchtend grün
    else if (minAge < 6)  color = '#a3e635';   // recent — limetten-grün
    else if (minAge < 24) color = '#fbbf24';   // älter — gelb
    else                  color = '#94a3b8';   // tagealt — grau

    // Halbtransparente Hüllkurve plus dezenter Linien-Outline.
    L.polygon(pts, {
      color, weight: 2, opacity: 0.7,
      fillColor: color, fillOpacity: 0.10,
      interactive: false,
    }).addTo(coverageLayer);

    // Punkte pro Bin als kleine Marker mit Tooltip (Az/Distanz/Count)
    for (const b of data.bins) {
      L.circleMarker([b.far_lat, b.far_lon], {
        radius: 3, color, weight: 1, opacity: 0.8,
        fillColor: color, fillOpacity: 0.5,
      }).bindTooltip(
        `${b.azimuth_deg}° · ${b.distance_km} km · ${b.report_count} Reports · ${b.latest_age_h}h alt`,
        { direction: 'top' }
      ).addTo(coverageLayer);
    }
  }

  // Maidenhead-Locator-Raster — Field (20°×10°) bei Zoom <5,
  // Square (2°×1°) ab Zoom 5. Subsquare wird zu dicht, lassen wir weg.
  function renderGrid() {
    if (!gridLayer) return;
    gridLayer.clearLayers();
    if (!showGrid) return;
    const z = map.getZoom();
    const isField = z < 5;
    const lonStep = isField ? 20 : 2;
    const latStep = isField ? 10 : 1;
    const b = map.getBounds();
    // Clip to visible region to keep the layer light.
    const wLon = Math.floor(b.getWest()  / lonStep) * lonStep;
    const eLon = Math.ceil (b.getEast()  / lonStep) * lonStep;
    const sLat = Math.max(-90, Math.floor(b.getSouth() / latStep) * latStep);
    const nLat = Math.min( 90, Math.ceil (b.getNorth() / latStep) * latStep);
    const style = { color: '#64748b', weight: isField ? 1 : 0.5, opacity: 0.5 };

    for (let lon = wLon; lon <= eLon; lon += lonStep) {
      L.polyline([[sLat, lon], [nLat, lon]], style).addTo(gridLayer);
    }
    for (let lat = sLat; lat <= nLat; lat += latStep) {
      L.polyline([[lat, b.getWest()], [lat, b.getEast()]], style).addTo(gridLayer);
    }
    // Field-Labels nur im Field-Modus, sonst Bildschirm-Spam
    if (isField) {
      for (let lon = wLon; lon < eLon; lon += 20) {
        for (let lat = sLat; lat < nLat; lat += 10) {
          const fLon = String.fromCharCode(65 + Math.floor((lon + 180) / 20));
          const fLat = String.fromCharCode(65 + Math.floor((lat + 90) / 10));
          L.marker([lat + 5, lon + 10], {
            icon: L.divIcon({
              className: 'ft8-grid-label',
              html: `<span>${fLon}${fLat}</span>`,
              iconSize: [30, 14], iconAnchor: [15, 7],
            }),
            interactive: false,
          }).addTo(gridLayer);
        }
      }
    }
  }

  // ----- Sortable station list (tabular companion to the map) -----
  let stationFilter = $state('');
  let stationSortBy = $state('call');
  let stationSortDir = $state('asc');

  function toggleSort(col) {
    if (stationSortBy === col) {
      stationSortDir = stationSortDir === 'asc' ? 'desc' : 'asc';
    } else { stationSortBy = col; stationSortDir = 'asc'; }
  }
  function sortArr(col) {
    return stationSortBy === col
      ? (stationSortDir === 'asc' ? ' ▲' : ' ▼') : '';
  }
  function shortDate(iso) {
    if (!iso) return '—';
    const d = parseUtc(iso);
    if (!d) return '—';
    const now = new Date();
    const iso2 = d.toISOString();
    const sameYear = d.getUTCFullYear() === now.getUTCFullYear();
    // DD.MM[.YY] HH:MM in UTC
    const datePart = `${iso2.slice(8,10)}.${iso2.slice(5,7)}` +
                     (sameYear ? '' : `.${iso2.slice(2,4)}`);
    return `${datePart} ${iso2.slice(11,16)}`;
  }
  function panTo(m) {
    map.flyTo([m.lat, m.lon], Math.max(map.getZoom(), 4));
  }

  const sortedFilteredStations = $derived.by(() => {
    const f = stationFilter.toUpperCase().trim();
    let list = mapStore.data.markers;
    if (f) {
      list = list.filter(m =>
        m.call.toUpperCase().includes(f) || (m.grid ?? '').toUpperCase().includes(f)
      );
    }
    const k = stationSortBy;
    const dir = stationSortDir === 'asc' ? 1 : -1;
    const value = (m) => {
      if (k === '_when') return m.last_worked ?? m.last_seen ?? '';
      return m[k] ?? '';
    };
    return [...list].sort((a, b) => {
      const va = value(a), vb = value(b);
      if (va < vb) return -1 * dir;
      if (va > vb) return  1 * dir;
      return 0;
    });
  });
</script>

<div class="wrap">
  <div class="controls">
    <div class="modes">
      <button class:active={mapStore.mode === 'all'}
              onclick={() => mapStore.setMode('all')}>Alle</button>
      <button class:active={mapStore.mode === 'worked'}
              onclick={() => mapStore.setMode('worked')}>Gearbeitet</button>
      <button class:active={mapStore.mode === 'heard'}
              onclick={() => mapStore.setMode('heard')}>Gehört</button>
    </div>
    <div class="legend">
      <span class="dot" style="background: {COLOURS.worked}"></span> gearbeitet
      <span class="dot" style="background: {COLOURS.heard}"></span> gehört
      <span class="dot" style="background: {COLOURS.both}"></span> beides
      <span class="dot" style="background: {COLOURS.operator}"></span> du
    </div>
  </div>

  <div class="layers">
    <label><input type="checkbox" bind:checked={showStations}/> Stationen</label>
    <label><input type="checkbox" bind:checked={showLiveArcs}/>
      <span class="dot-inline" style="background:{COLOURS.live}"></span>
      Live-Decodes (Großkreis)
    </label>
    <label><input type="checkbox" bind:checked={showHeatmap}/> Heard-Heatmap</label>
    <label><input type="checkbox" bind:checked={showOperatingLocations}/>
      <span class="dot-inline" style="background:{COLOURS.loc}"></span>
      Eigene Standorte
    </label>
    <label><input type="checkbox" bind:checked={showDxCluster}/>
      <span class="dot-inline" style="background:{COLOURS.dx}"></span>
      DX-Cluster
    </label>
    <label><input type="checkbox" bind:checked={showGrid}/>
      📐 Locator-Raster
    </label>
    <label><input type="checkbox" bind:checked={showTerminator}/>
      🌗 Gray-Line (Tag/Nacht)
    </label>
    <label><input type="checkbox" bind:checked={showCoverage}/>
      📡 Coverage (wer hört mich)
    </label>
    {#if showCoverage}
      <label class="inline-filter">
        Zeitraum
        <select bind:value={coverageHours}>
          <option value={1}>1 h</option>
          <option value={6}>6 h</option>
          <option value={24}>24 h</option>
          <option value={72}>3 Tage</option>
          <option value={168}>7 Tage</option>
        </select>
      </label>
      <label class="inline-filter">
        Band
        <select bind:value={coverageBand}>
          <option value="">alle</option>
          <option value="160m">160m</option>
          <option value="80m">80m</option>
          <option value="60m">60m</option>
          <option value="40m">40m</option>
          <option value="30m">30m</option>
          <option value="20m">20m</option>
          <option value="17m">17m</option>
          <option value="15m">15m</option>
          <option value="12m">12m</option>
          <option value="10m">10m</option>
          <option value="6m">6m</option>
        </select>
      </label>
    {/if}
  </div>

  <div class="map" bind:this={el}></div>

  <div class="footer">
    <span>{mapStore.data.markers.length} Stationen</span>
    {#if mapStore.mode !== 'worked'}
      <label>
        Gehört letzte
        <select value={mapStore.minutesHeard}
                onchange={(e) => mapStore.setMinutesHeard(parseInt(e.target.value))}>
          <option value={15}>15 min</option>
          <option value={60}>60 min</option>
          <option value={360}>6 h</option>
          <option value={1440}>24 h</option>
        </select>
      </label>
    {/if}
  </div>

  <!-- Sortable station list — tabular twin of the markers -->
  <details class="station-list" open>
    <summary>Stationen als Liste ({sortedFilteredStations.length}/{mapStore.data.markers.length})</summary>
    <input type="text" placeholder="Call / Grid filtern…"
           bind:value={stationFilter} class="filter"/>
    <div class="list-wrap">
      <table>
        <thead>
          <tr>
            <th class="sort" onclick={() => toggleSort('call')}>Call{sortArr('call')}</th>
            <th class="sort" onclick={() => toggleSort('kind')}>Typ{sortArr('kind')}</th>
            <th class="sort" onclick={() => toggleSort('grid')}>Grid{sortArr('grid')}</th>
            <th class="sort" onclick={() => toggleSort('band')}>Band{sortArr('band')}</th>
            <th class="sort" onclick={() => toggleSort('snr_best')}>SNR{sortArr('snr_best')}</th>
            <th class="sort" onclick={() => toggleSort('count')}>Hits{sortArr('count')}</th>
            <th class="sort" onclick={() => toggleSort('_when')}>Wann{sortArr('_when')}</th>
          </tr>
        </thead>
        <tbody>
          {#each sortedFilteredStations as m (m.call)}
            <tr onclick={() => panTo(m)} class="row">
              <td><strong>{m.call}</strong></td>
              <td><span class="kind" style="background: {COLOURS[m.kind]}; color: #0f172a">{m.kind}</span></td>
              <td class="mono">{m.grid}</td>
              <td>{m.band ?? ''}</td>
              <td class="num">{m.snr_best ?? ''}</td>
              <td class="num">{m.count ?? 1}</td>
              <td class="mono">{shortDate(m.last_worked ?? m.last_seen)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </details>
</div>

<style>
  .wrap { background: var(--panel); border-radius: 8px; padding: 0.6rem; }
  .controls {
    display: flex; gap: 1rem; flex-wrap: wrap;
    align-items: center; justify-content: space-between;
    margin-bottom: 0.5rem;
  }
  .modes { display: flex; gap: 0.3rem; }
  .modes button {
    background: transparent; color: var(--fg); border: 1px solid #334155;
    border-radius: 6px; padding: 0.3rem 0.7rem; cursor: pointer; font-size: 0.85rem;
  }
  .modes button.active { background: var(--accent); color: #0f172a; font-weight: 600; }
  .legend { display: flex; gap: 0.6rem; font-size: 0.8rem; color: #94a3b8;
            align-items: center; flex-wrap: wrap; }
  .legend .dot { display: inline-block; width: 10px; height: 10px;
                 border-radius: 50%; margin-right: 0.2rem; vertical-align: middle; }
  .dot-inline { display: inline-block; width: 9px; height: 9px;
                border-radius: 50%; margin: 0 0.2rem; vertical-align: middle; }
  .layers {
    display: flex; gap: 0.7rem; flex-wrap: wrap; margin-bottom: 0.4rem;
    font-size: 0.85rem; color: #cbd5e1;
  }
  .layers label { display: inline-flex; align-items: center; gap: 0.3rem; cursor: pointer; }
  .layers .inline-filter { font-size: 0.75rem; color: #94a3b8; gap: 0.2rem; }
  .layers .inline-filter select {
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 3px; padding: 0.1rem 0.2rem; font-size: 0.75rem;
  }
  .map { height: 55vh; width: 100%; border-radius: 8px; background: #0b1220; }
  .footer {
    display: flex; justify-content: space-between; align-items: center;
    margin-top: 0.4rem; font-size: 0.85rem; color: #94a3b8;
  }
  .footer select { background: #0b1220; color: var(--fg);
                   border: 1px solid #334155; border-radius: 4px; padding: 0.2rem; }
  :global(.ft8-marker span) {
    display: block; border-radius: 50%;
    border: 2px solid white; box-shadow: 0 0 4px rgba(0,0,0,0.5);
  }
  :global(.ft8-operator span) {
    display: block; width: 18px; height: 18px; border-radius: 50%;
    background: #ec4899; border: 3px solid white;
    box-shadow: 0 0 8px rgba(236,72,153,0.6);
  }
  :global(.ft8-grid-label span) {
    display: inline-block; padding: 1px 3px; border-radius: 2px;
    background: rgba(15,23,42,0.6); color: #94a3b8;
    font-size: 0.65rem; font-weight: 600; line-height: 1;
    pointer-events: none; user-select: none;
  }
  :global(.ft8-subsolar span) {
    display: block; font-size: 18px; line-height: 1;
    text-align: center; text-shadow: 0 0 4px #fbbf24;
    pointer-events: none;
  }

  /* Station list */
  .station-list { margin-top: 0.7rem; }
  .station-list summary {
    cursor: pointer; color: var(--accent); font-size: 0.9rem;
    padding: 0.4rem; background: rgba(15,23,42,0.5); border-radius: 6px;
  }
  .station-list .filter {
    width: 100%; margin: 0.5rem 0 0.3rem;
    background: #0b1220; color: var(--fg); border: 1px solid #334155;
    border-radius: 4px; padding: 0.4rem 0.6rem; font-size: 0.85rem;
  }
  .list-wrap { max-height: 30vh; overflow-y: auto; }
  .station-list table {
    width: 100%; border-collapse: collapse; font-size: 0.8rem;
    font-family: ui-monospace, monospace;
  }
  .station-list th {
    text-align: left; padding: 0.3rem 0.4rem;
    background: rgba(15,23,42,0.7); color: #94a3b8;
    text-transform: uppercase; font-size: 0.65rem; letter-spacing: 0.05em;
    position: sticky; top: 0; cursor: pointer; user-select: none;
  }
  .station-list th.sort:hover { color: var(--accent); }
  .station-list td {
    padding: 0.25rem 0.4rem; border-bottom: 1px solid #1e293b;
  }
  .station-list tr.row { cursor: pointer; }
  .station-list tr.row:hover { background: rgba(56,189,248,0.05); }
  .station-list .kind {
    padding: 0.05em 0.4em; border-radius: 3px; font-size: 0.7rem;
    font-weight: 700; text-transform: uppercase;
  }
  .station-list .mono { font-family: ui-monospace, monospace; color: #94a3b8; }
  .station-list .num  { text-align: right; color: #cbd5e1; }
</style>
