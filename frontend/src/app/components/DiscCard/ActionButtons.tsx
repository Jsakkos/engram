/**
 * Action buttons component for disc cards (Cancel, Review, Re-Identify)
 */

import { motion } from "motion/react";
import { X, AlertTriangle, RefreshCw } from "lucide-react";
import type { DiscState } from "../DiscCard";

interface ActionButtonsProps {
    state: DiscState;
    isHovered: boolean;
    onCancel?: () => void;
    onReview?: () => void;
    onReIdentify?: () => void;
}

export function ActionButtons({ state, isHovered, onCancel, onReview, onReIdentify }: ActionButtonsProps) {
    return (
        <div className="flex items-center gap-2">
            {/* Cancel Button */}
            {onCancel && (isHovered || ['scanning', 'ripping', 'processing'].includes(state)) && (
                <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    onClick={onCancel}
                    className="p-2 border-2 border-red-500/50 bg-navy-900/80 text-red-400 hover:bg-red-500/20 hover:border-red-500 transition-all"
                    title="Cancel Job"
                    aria-label="Cancel job"
                    style={{ boxShadow: "0 0 10px rgba(239, 68, 68, 0.3)" }}
                >
                    <X className="w-4 h-4" />
                </motion.button>
            )}

            {/* Wrong Title Button */}
            {onReIdentify && (
                <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    onClick={(e) => { e.stopPropagation(); onReIdentify(); }}
                    className="px-3 py-2 border-2 border-cyan-500/50 bg-navy-900/80 text-cyan-400 hover:bg-cyan-500/20 hover:border-cyan-400 transition-all font-mono font-bold text-[10px] uppercase tracking-wider flex items-center gap-1.5"
                    style={{ boxShadow: "0 0 10px rgba(6, 182, 212, 0.3)" }}
                    aria-label="Wrong title — re-identify disc"
                >
                    <RefreshCw className="w-3 h-3" />
                    <span>Wrong Title?</span>
                </motion.button>
            )}

            {/* Review Button */}
            {onReview && state === 'review_needed' && (
                <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    onClick={onReview}
                    className="px-4 py-2 border-2 border-yellow-500 bg-navy-900/80 text-yellow-400 hover:bg-yellow-500/20 hover:border-yellow-400 transition-all font-mono font-bold text-xs uppercase tracking-wider flex items-center gap-2"
                    style={{ boxShadow: "0 0 15px rgba(234, 179, 8, 0.5)" }}
                    aria-label="Review needed — open review queue"
                >
                    <AlertTriangle className="w-4 h-4" />
                    <span>REVIEW NEEDED</span>
                </motion.button>
            )}
        </div>
    );
}
