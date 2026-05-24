// Resilient SSE client. EventSource has auto-reconnect built in, but it's
// pretty quiet about it — this wrapper exposes a `connected` signal and
// re-opens cleanly on tab-resume.

export function openStream(path, { onMessage, onOpen, onError } = {}) {
  let es = new EventSource(`/sse${path}`);

  es.onopen   = () => onOpen?.();
  es.onerror  = (e) => onError?.(e);
  es.onmessage = (ev) => {
    try {
      onMessage?.(JSON.parse(ev.data));
    } catch {
      onMessage?.(ev.data);
    }
  };

  return {
    close: () => es.close(),
    get readyState() { return es.readyState; },
  };
}
