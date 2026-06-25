/**
 * WebSocket client with auto-reconnect.
 * Dispatches events to registered handlers.
 */

const WS_URL = process.env.NODE_ENV === 'production'
  ? `ws://${window.location.host}/ws`
  : 'ws://localhost:8000/ws';

let socket = null;
let handlers = {};
let reconnectTimer = null;
let pingTimer = null;

export function connect(onEvent) {
  if (socket && socket.readyState === WebSocket.OPEN) return;

  socket = new WebSocket(WS_URL);

  socket.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (onEvent) onEvent(msg);
    } catch {}
  };

  socket.onclose = () => {
    clearInterval(pingTimer);
    reconnectTimer = setTimeout(() => connect(onEvent), 3000);
  };

  socket.onerror = () => socket.close();

  socket.onopen = () => {
    clearTimeout(reconnectTimer);
    pingTimer = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) socket.send('ping');
    }, 20000);
  };
}

export function disconnect() {
  clearTimeout(reconnectTimer);
  clearInterval(pingTimer);
  if (socket) socket.close();
}
