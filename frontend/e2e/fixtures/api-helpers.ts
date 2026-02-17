/**
 * Helper functions for calling simulation endpoints in E2E tests.
 */

const API_BASE = 'http://localhost:8000';

export async function simulateInsertDisc(params: Record<string, unknown>): Promise<{ status: string; job_id: number }> {
    const res = await fetch(`${API_BASE}/api/simulate/insert-disc`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(params),
    });
    if (!res.ok) {
        throw new Error(`Failed to simulate disc insert: ${res.status} ${await res.text()}`);
    }
    return res.json();
}

export async function simulateRemoveDisc(driveId: string = 'E:'): Promise<void> {
    const res = await fetch(`${API_BASE}/api/simulate/remove-disc?drive_id=${encodeURIComponent(driveId)}`, {
        method: 'POST',
    });
    if (!res.ok) {
        throw new Error(`Failed to simulate disc removal: ${res.status}`);
    }
}

export async function advanceJob(jobId: number): Promise<{ new_state: string }> {
    const res = await fetch(`${API_BASE}/api/simulate/advance-job/${jobId}`, {
        method: 'POST',
    });
    if (!res.ok) {
        throw new Error(`Failed to advance job: ${res.status}`);
    }
    return res.json();
}

export async function clearCompletedJobs(): Promise<void> {
    const res = await fetch(`${API_BASE}/api/jobs/completed`, {
        method: 'DELETE',
    });
    if (!res.ok) {
        throw new Error(`Failed to clear completed: ${res.status}`);
    }
}

export async function resetAllJobs(): Promise<void> {
    const res = await fetch(`${API_BASE}/api/simulate/reset-all-jobs`, {
        method: 'DELETE',
    });
    if (!res.ok) {
        throw new Error(`Failed to reset all jobs: ${res.status}`);
    }
}

export async function getJobs(): Promise<unknown[]> {
    const res = await fetch(`${API_BASE}/api/jobs`);
    if (!res.ok) {
        throw new Error(`Failed to get jobs: ${res.status}`);
    }
    return res.json();
}

export async function simulateInsertDiscFromStaging(params: {
    staging_path: string;
    volume_label?: string;
    content_type?: string;
    detected_title?: string;
    detected_season?: number;
    rip_speed_multiplier?: number;
}): Promise<{ status: string; job_id: number; titles_count: number }> {
    const query = new URLSearchParams();
    query.set('staging_path', params.staging_path);
    if (params.volume_label) query.set('volume_label', params.volume_label);
    if (params.content_type) query.set('content_type', params.content_type);
    if (params.detected_title) query.set('detected_title', params.detected_title);
    if (params.detected_season != null) query.set('detected_season', String(params.detected_season));
    if (params.rip_speed_multiplier != null) query.set('rip_speed_multiplier', String(params.rip_speed_multiplier));

    const res = await fetch(`${API_BASE}/api/simulate/insert-disc-from-staging?${query}`, {
        method: 'POST',
    });
    if (!res.ok) {
        throw new Error(`Failed to simulate disc from staging: ${res.status} ${await res.text()}`);
    }
    return res.json();
}
