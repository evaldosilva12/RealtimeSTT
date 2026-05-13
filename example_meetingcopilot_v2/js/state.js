export const state = {
    socket: null,
    sessionId: null,
    connected: false,
    utterances: new Map(),
    responses: new Map(),
    setup: null,
    hiddenContext: [],
};

export function newestUtterances(count = 4) {
    return [...state.utterances.values()]
        .filter((item) => item.status === "final" && item.source === "other")
        .sort((a, b) => b.created_at - a.created_at)
        .slice(0, count)
        .reverse();
}
