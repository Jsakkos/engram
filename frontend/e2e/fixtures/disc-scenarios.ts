/**
 * Predefined disc simulation payloads for E2E tests.
 */

export const TV_DISC_ARRESTED_DEVELOPMENT = {
    drive_id: 'E:',
    volume_label: 'ARRESTED_DEVELOPMENT_S1D1',
    content_type: 'tv',
    detected_title: 'Arrested Development',
    detected_season: 1,
    simulate_ripping: true,
    rip_speed_multiplier: 50,
    titles: [
        { duration_seconds: 1320, file_size_bytes: 1073741824, chapter_count: 5 },
        { duration_seconds: 1290, file_size_bytes: 1048576000, chapter_count: 5 },
        { duration_seconds: 1350, file_size_bytes: 1100000000, chapter_count: 6 },
        { duration_seconds: 1280, file_size_bytes: 1040000000, chapter_count: 5 },
        { duration_seconds: 1310, file_size_bytes: 1060000000, chapter_count: 5 },
        { duration_seconds: 1340, file_size_bytes: 1090000000, chapter_count: 6 },
        { duration_seconds: 1300, file_size_bytes: 1050000000, chapter_count: 5 },
        { duration_seconds: 1330, file_size_bytes: 1080000000, chapter_count: 5 },
    ],
};

export const MOVIE_DISC = {
    drive_id: 'E:',
    volume_label: 'INCEPTION_2010',
    content_type: 'movie',
    detected_title: 'Inception',
    detected_season: null,
    simulate_ripping: true,
    rip_speed_multiplier: 50,
    titles: [
        { duration_seconds: 8880, file_size_bytes: 35433480192, chapter_count: 25 },
    ],
};

export const TV_DISC_ARRESTED_DEVELOPMENT_REAL = {
    staging_path: 'C:/Video/ARRESTED_Development_S1D1',
    volume_label: 'ARRESTED_DEVELOPMENT_S1D1',
    content_type: 'tv',
    detected_title: 'Arrested Development',
    detected_season: 1,
    rip_speed_multiplier: 5,
};

export const AMBIGUOUS_DISC = {
    drive_id: 'E:',
    volume_label: 'UNKNOWN_DISC',
    content_type: 'unknown',
    detected_title: null,
    detected_season: null,
    simulate_ripping: false,
    titles: [
        { duration_seconds: 1800, file_size_bytes: 1500000000, chapter_count: 3 },
        { duration_seconds: 7200, file_size_bytes: 4000000000, chapter_count: 20 },
        { duration_seconds: 1800, file_size_bytes: 1500000000, chapter_count: 3 },
    ],
};

// --- Realistic disc scenarios based on actual disc metadata ---

/** Generic label disc (The Italian Job) — triggers NamePromptModal for user to provide name */
export const GENERIC_LABEL_DISC = {
    drive_id: 'E:',
    volume_label: 'LOGICAL_VOLUME_ID',
    content_type: 'movie',
    detected_title: null,
    detected_season: null,
    simulate_ripping: false,
    force_review_needed: true,
    review_reason: 'Disc label unreadable',
    titles: [
        { duration_seconds: 6632, file_size_bytes: 19755906658, chapter_count: 16 },
        { duration_seconds: 525, file_size_bytes: 333769756, chapter_count: 6 },
        { duration_seconds: 242, file_size_bytes: 154275563, chapter_count: 0 },
        { duration_seconds: 355, file_size_bytes: 211782029, chapter_count: 0 },
        { duration_seconds: 339, file_size_bytes: 260771528, chapter_count: 0 },
        { duration_seconds: 1098, file_size_bytes: 828631029, chapter_count: 0 },
        { duration_seconds: 337, file_size_bytes: 257195801, chapter_count: 0 },
        { duration_seconds: 473, file_size_bytes: 369338749, chapter_count: 0 },
        { duration_seconds: 348, file_size_bytes: 258871259, chapter_count: 0 },
        { duration_seconds: 173, file_size_bytes: 358740177, chapter_count: 0 },
    ],
};

/** Multi-feature movie (The Terminator) — 2 identical features at 1080p, needs review */
export const MULTI_FEATURE_MOVIE_DISC = {
    drive_id: 'E:',
    volume_label: 'THE TERMINATOR',
    content_type: 'movie',
    detected_title: 'The Terminator',
    detected_season: null,
    simulate_ripping: true,
    rip_speed_multiplier: 50,
    titles: [
        { duration_seconds: 599, file_size_bytes: 611418867, chapter_count: 7 },
        { duration_seconds: 6423, file_size_bytes: 30614003858, chapter_count: 32 },
        { duration_seconds: 6423, file_size_bytes: 31513883840, chapter_count: 32 },
        { duration_seconds: 599, file_size_bytes: 611855206, chapter_count: 7 },
        { duration_seconds: 1230, file_size_bytes: 1269728373, chapter_count: 0 },
        { duration_seconds: 260, file_size_bytes: 267624118, chapter_count: 0 },
        { duration_seconds: 778, file_size_bytes: 792752907, chapter_count: 0 },
    ],
};

/** TV disc with Play All + extras (Star Trek Picard S1D3) */
export const TV_DISC_PICARD_S1D3 = {
    drive_id: 'E:',
    volume_label: 'STAR TREK PICARD S1D3',
    content_type: 'tv',
    detected_title: 'Star Trek Picard',
    detected_season: 1,
    simulate_ripping: true,
    rip_speed_multiplier: 50,
    titles: [
        { duration_seconds: 3395, file_size_bytes: 11659264873, chapter_count: 6 },
        { duration_seconds: 2696, file_size_bytes: 9242778731, chapter_count: 6 },
        { duration_seconds: 3325, file_size_bytes: 11375319742, chapter_count: 6 },
        { duration_seconds: 9416, file_size_bytes: 32277351940, chapter_count: 18 },
        { duration_seconds: 306, file_size_bytes: 853769261, chapter_count: 0 },
    ],
};

/** Arrested Development S1D1 with real metadata — 8 episodes, 3 extras */
export const TV_DISC_ARRESTED_DEV_REALISTIC = {
    drive_id: 'E:',
    volume_label: 'ARRESTED_Development_S1D1',
    content_type: 'tv',
    detected_title: 'Arrested Development',
    detected_season: 1,
    simulate_ripping: true,
    rip_speed_multiplier: 50,
    titles: [
        { duration_seconds: 1714, file_size_bytes: 668897237, chapter_count: 5 },
        { duration_seconds: 1307, file_size_bytes: 524788760, chapter_count: 5 },
        { duration_seconds: 1326, file_size_bytes: 580133133, chapter_count: 5 },
        { duration_seconds: 1332, file_size_bytes: 533503132, chapter_count: 5 },
        { duration_seconds: 1306, file_size_bytes: 519383009, chapter_count: 5 },
        { duration_seconds: 1305, file_size_bytes: 540108878, chapter_count: 5 },
        { duration_seconds: 1306, file_size_bytes: 519952776, chapter_count: 5 },
        { duration_seconds: 1714, file_size_bytes: 668897237, chapter_count: 5 },
        { duration_seconds: 391, file_size_bytes: 153142769, chapter_count: 4 },
        { duration_seconds: 149, file_size_bytes: 56802881, chapter_count: 0 },
        { duration_seconds: 996, file_size_bytes: 363861577, chapter_count: 0 },
    ],
};
