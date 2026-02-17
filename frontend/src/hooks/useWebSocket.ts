import { useState, useEffect, useRef, useCallback } from 'react';
import { WebSocketMessage } from '../types';
import { UI_CONFIG } from '../config/constants';

interface UseWebSocketReturn {
    lastMessage: WebSocketMessage | null;
    isConnected: boolean;
    sendMessage: (message: string) => void;
}

export function useWebSocket(url: string): UseWebSocketReturn {
    const [lastMessage, setLastMessage] = useState<WebSocketMessage | null>(null);
    const [isConnected, setIsConnected] = useState(false);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<number | null>(null);

    const connect = useCallback(() => {
        try {
            const ws = new WebSocket(url);

            ws.onopen = () => {
                console.log('WebSocket connected');
                setIsConnected(true);
            };

            ws.onclose = () => {
                console.log('WebSocket disconnected');
                setIsConnected(false);

                // Reconnect after configured delay
                reconnectTimeoutRef.current = window.setTimeout(() => {
                    console.log('Attempting to reconnect...');
                    connect();
                }, UI_CONFIG.WEBSOCKET_RECONNECT_DELAY_MS);
            };

            ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };

            ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data) as WebSocketMessage;
                    setLastMessage(data);
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };

            wsRef.current = ws;
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
        }
    }, [url]);

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

    return { lastMessage, isConnected, sendMessage };
}
