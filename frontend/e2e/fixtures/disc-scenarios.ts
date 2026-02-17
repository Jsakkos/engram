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
