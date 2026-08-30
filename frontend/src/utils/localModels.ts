/**
 * Model discovery for local AI providers (Ollama, LM Studio).
 *
 * Both servers expose the OpenAI-compatible GET /v1/models, which the backend
 * proxies at /api/ai/local-models. That endpoint never returns 5xx: an
 * unreachable server is the normal state while a user is still setting up, so
 * failure arrives as a populated `error` alongside an empty list and the caller
 * degrades to a free-text model field rather than showing a banner.
 */

export const LOCAL_PROVIDERS = ['ollama', 'lmstudio'] as const;

export type LocalProvider = (typeof LOCAL_PROVIDERS)[number];

/** Mirrors LOCAL_DEFAULT_BASE_URLS in backend/app/core/ai_client.py. The backend
 *  is authoritative; these are placeholders and pre-fills only. */
const DEFAULT_URLS: Record<string, string> = {
    ollama: 'http://localhost:11434/v1',
    lmstudio: 'http://localhost:1234/v1',
};

export function isLocalProvider(provider: string): boolean {
    return (LOCAL_PROVIDERS as readonly string[]).includes(provider);
}

export function localProviderDefaultUrl(provider: string): string {
    return DEFAULT_URLS[provider] ?? '';
}

export interface LocalModelsResult {
    models: string[];
    error: string | null;
}

export async function fetchLocalModels(
    provider: string,
    baseUrl: string,
): Promise<LocalModelsResult> {
    const params = new URLSearchParams({ provider });
    const trimmed = baseUrl.trim();
    if (trimmed) params.set('base_url', trimmed);

    let response: Response;
    try {
        response = await fetch(`/api/ai/local-models?${params.toString()}`);
    } catch (err) {
        console.error('Local model list request failed (network):', err);
        return {
            models: [],
            error: "Couldn't reach the backend to list models.",
        };
    }

    if (!response.ok) {
        console.error(`Local model list returned HTTP ${response.status}`);
        return {
            models: [],
            error: `Couldn't list models (HTTP ${response.status}).`,
        };
    }

    try {
        const body = await response.json();
        return {
            models: Array.isArray(body.models) ? body.models : [],
            error: body.error ?? null,
        };
    } catch (err) {
        console.error('Local model list returned an unparseable response:', err);
        return { models: [], error: "Couldn't read the model list." };
    }
}
