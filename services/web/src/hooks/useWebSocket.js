import { useEffect, useRef } from "react";

export function useWebSocket({ enabled, onMessage }) {
  const wsRef = useRef(null);
  const timerRef = useRef(null);
  const url = import.meta.env.VITE_WS_URL;

  useEffect(() => {
    if (!enabled) return undefined;

    if (url) {
      const ws = new WebSocket(url);
      wsRef.current = ws;
      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          onMessage?.(payload);
        } catch {
          onMessage?.({ type: "raw", data: event.data });
        }
      };
      ws.onerror = () => {
        onMessage?.({ type: "error", message: "websocket error" });
      };
      return () => {
        ws.close();
      };
    }

    // Placeholder heartbeat stream for local mode without websocket server.
    timerRef.current = window.setInterval(() => {
      onMessage?.({ type: "heartbeat", ts: new Date().toISOString() });
    }, 5000);

    return () => {
      if (timerRef.current) {
        clearInterval(timerRef.current);
      }
    };
  }, [enabled, onMessage, url]);
}
