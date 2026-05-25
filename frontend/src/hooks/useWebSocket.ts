import { useState, useEffect, useRef, useCallback } from 'react';
import { WebSocketMessage } from '../types';
import { UI_CONFIG } from '../config/constants';

type MessageListener = (msg: WebSocketMessage) => void;

interface UseWebSocketOptions {
    /**
     * Called every time the socket (re)connects successfully. Use this to
     * resync state that may have drifted while disconnected.
     */
    onOpen?: () => void;
}

interface UseWebSocketReturn {
    isConnected: boolean;
    sendMessage: (message: string) => void;
    addMessageListener: (listener: MessageListener) => () => void;
}

export function useWebSocket(url: string, options: UseWebSocketOptions = {}): UseWebSocketReturn {
    const [isConnected, setIsConnected] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<number | null>(null);
    const listenersRef = useRef<Set<MessageListener>>(new Set());
    // Current backoff delay; grows on each failed/closed connection, resets on open.
    const backoffRef = useRef<number>(UI_CONFIG.WEBSOCKET_RECONNECT_BASE_DELAY_MS);
    // Keep the latest onOpen callback without re-creating `connect` (avoids reconnect storms).
    const onOpenRef = useRef<(() => void) | undefined>(options.onOpen);
    onOpenRef.current = options.onOpen;

    const scheduleReconnect = useCallback((connect: () => void) => {
        const delay = backoffRef.current;
        // Exponential backoff, capped.
        backoffRef.current = Math.min(
            backoffRef.current * 2,
            UI_CONFIG.WEBSOCKET_RECONNECT_MAX_DELAY_MS,
        );
        reconnectTimeoutRef.current = window.setTimeout(() => {
            console.log(`Attempting to reconnect (next backoff ${backoffRef.current}ms)...`);
            connect();
        }, delay);
    }, []);

    const connect = useCallback(() => {
        try {
            const ws = new WebSocket(url);

            ws.onopen = () => {
                console.log('WebSocket connected');
                // Reset backoff on a successful connection.
                backoffRef.current = UI_CONFIG.WEBSOCKET_RECONNECT_BASE_DELAY_MS;
                setIsConnected(true);
                onOpenRef.current?.();
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                setIsConnected(false);
                scheduleReconnect(connect);
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data) as WebSocketMessage;
                    // Invoke all registered listeners synchronously — zero message loss
                    listenersRef.current.forEach(listener => listener(data));
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };

            wsRef.current = ws;
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            // Couldn't even construct the socket — retry with backoff.
            scheduleReconnect(connect);
        }
    }, [url, scheduleReconnect]);

    useEffect(() => {
        connect();

        return () => {
            if (reconnectTimeoutRef.current) {
                window.clearTimeout(reconnectTimeoutRef.current);
            }
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [connect]);

    const sendMessage = useCallback((message: string) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(message);
        }
    }, []);

    const addMessageListener = useCallback((listener: MessageListener) => {
        listenersRef.current.add(listener);
        return () => { listenersRef.current.delete(listener); };
    }, []);

    return { isConnected, sendMessage, addMessageListener };
}
