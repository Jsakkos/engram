/**
 * Mock data generator for development mode
 */

import type { DiscData } from '../components/DiscCard';

export function generateMockDiscs(): DiscData[] {
  return [
    // Game of Thrones - complex parallel ripping/matching
    {
      id: "1",
      title: "Game of Thrones: Season 3",
      subtitle: "Blu-ray • Disc 1 • Fantasy Drama",
      discLabel: "GOT_S3_D1",
      coverUrl: "https://images.unsplash.com/photo-1659835347242-97835d671db7?w=400",
      mediaType: "tv",
      state: "ripping",
      progress: 35,
      currentSpeed: "6.5x",
      etaSeconds: 1840,
      isoProgress: 100,
      tracks: [
        {
          id: "t1",
          title: "Episode 1",
          duration: "54:32",
          state: "matched",
          progress: 100,
          finalMatch: "S03E01 - Valar Dohaeris",
        },
        {
          id: "t2",
          title: "Episode 2",
          duration: "56:12",
          state: "matched",
          progress: 100,
          finalMatch: "S03E02 - Dark Wings, Dark Words",
        },
        {
          id: "t3",
          title: "Episode 3",
          duration: "53:01",
          state: "matching",
          progress: 78,
          matchCandidates: [
            { episode: "S03E03 - Walk of Punishment", votes: 3, targetVotes: 4, confidence: 0.82 },
            { episode: "S03E04 - And Now His Watch Is Ended", votes: 1, targetVotes: 4, confidence: 0.61 },
          ],
        },
        {
          id: "t4",
          title: "Episode 4",
          duration: "57:24",
          state: "matching",
          progress: 45,
        },
        {
          id: "t5",
          title: "Episode 5",
          duration: "57:18",
          state: "ripping",
          progress: 32,
        },
        {
          id: "t6",
          title: "Episode 6",
          duration: "55:47",
          state: "pending",
          progress: 0,
        },
      ],
    },

    // Inception - movie with subtitle download
    {
      id: "2",
      title: "Inception",
      subtitle: "4K UHD • 2010 • Sci-Fi Thriller",
      discLabel: "INCEPTION_UHD",
      coverUrl: "https://images.unsplash.com/photo-1536440136628-849c177e76a1?w=400",
      mediaType: "movie",
      state: "ripping",
      progress: 62,
      currentSpeed: "8.2x",
      etaSeconds: 920,
      isoProgress: 100,
      tracks: [
        {
          id: "m1",
          title: "Main Feature",
          duration: "2:28:17",
          state: "matching",
          progress: 62,
        },
      ],
    },

    // The Sopranos - scanning phase
    {
      id: "3",
      title: "The Sopranos: Season 1",
      subtitle: "DVD • Disc 2 • Crime Drama",
      discLabel: "SOPRANOS_S1_D2",
      coverUrl: "https://images.unsplash.com/photo-1574267432644-f65bdc3e661f?w=400",
      mediaType: "tv",
      state: "scanning",
      progress: 0,
      isoProgress: 0,
      tracks: [],
    },

    // Blade Runner 2049 - completed movie
    {
      id: "4",
      title: "Blade Runner 2049",
      subtitle: "Blu-ray • 2017 • Sci-Fi",
      discLabel: "BR2049",
      coverUrl: "https://images.unsplash.com/photo-1518676590629-3dcbd9c5a5c9?w=400",
      mediaType: "movie",
      state: "completed",
      progress: 100,
      isoProgress: 100,
      tracks: [
        {
          id: "m2",
          title: "Main Feature",
          duration: "2:44:00",
          state: "matched",
          progress: 100,
        },
      ],
    },
  ];
}
