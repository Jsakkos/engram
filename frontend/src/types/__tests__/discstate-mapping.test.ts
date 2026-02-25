/**
 * Tests for issue #16: Processing visibility.
 *
 * TDD: These tests verify that backend matching/organizing states are correctly
 * mapped to frontend DiscState so that the track grid remains visible during
 * processing phases.
 */

import { describe, it, expect } from 'vitest';
import { mapJobStateToDiscState, mapTitleStateToTrackState } from '../adapters';

describe('Issue #16: Processing state visibility', () => {
  describe('mapJobStateToDiscState', () => {
    it('should map "matching" to "processing" state', () => {
      const result = mapJobStateToDiscState('matching');
      expect(result).toBe('processing');
    });

    it('should map "organizing" to "processing" state', () => {
      const result = mapJobStateToDiscState('organizing');
      expect(result).toBe('processing');
    });

    it('should still map "ripping" to "ripping"', () => {
      const result = mapJobStateToDiscState('ripping');
      expect(result).toBe('ripping');
    });

    it('should map "identifying" to "scanning"', () => {
      const result = mapJobStateToDiscState('identifying');
      expect(result).toBe('scanning');
    });

    it('should map "completed" to "completed"', () => {
      const result = mapJobStateToDiscState('completed');
      expect(result).toBe('completed');
    });

    it('should map "failed" to "error"', () => {
      const result = mapJobStateToDiscState('failed');
      expect(result).toBe('error');
    });
  });

  describe('mapTitleStateToTrackState', () => {
    it('should map "matching" to "matching"', () => {
      expect(mapTitleStateToTrackState('matching')).toBe('matching');
    });

    it('should map "matched" to "matched"', () => {
      expect(mapTitleStateToTrackState('matched')).toBe('matched');
    });

    it('should map "completed" to "matched"', () => {
      expect(mapTitleStateToTrackState('completed')).toBe('matched');
    });

    it('should map "review" to "matched"', () => {
      expect(mapTitleStateToTrackState('review')).toBe('matched');
    });
  });
});
