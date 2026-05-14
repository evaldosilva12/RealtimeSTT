export const DEFAULT_WS_PORT = 8015;

export function getWebSocketUrl() {
    if (window.interviewCopilot?.webSocketUrl) {
        return window.interviewCopilot.webSocketUrl;
    }

    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const host = location.hostname || "localhost";
    return `${protocol}://${host}:${DEFAULT_WS_PORT}`;
}
