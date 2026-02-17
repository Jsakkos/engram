/**
 * Action buttons component for disc cards (Cancel, Review)
 */

import { motion } from "motion/react";
import { X, AlertTriangle } from "lucide-react";
import type { DiscState } from "../DiscCard";

interface ActionButtonsProps {
    state: DiscState;
    isHovered: boolean;
    onCancel?: () => void;
    onReview?: () => void;
}

export function ActionButtons({ state, isHovered, onCancel, onReview }: ActionButtonsProps) {
    return (
        <div className="flex items-center gap-2">
            {/* Cancel Button */}
            {onCancel && (isHovered || ['scanning', 'ripping'].includes(state)) && (
                <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    onClick={onCancel}
                    className="p-2 border-2 border-red-500/50 bg-black/80 text-red-400 hover:bg-red-500/20 hover:border-red-500 transition-all"
                    title="Cancel Job"
                    style={{ boxShadow: "0 0 10px rgba(239, 68, 68, 0.3)" }}
                >
                    <X className="w-4 h-4" />
                </motion.button>
            )}

            {/* Review Button */}
            {onReview && (
                <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    onClick={onReview}
                    className="px-4 py-2 border-2 border-yellow-500 bg-black/80 text-yellow-400 hover:bg-yellow-500/20 hover:border-yellow-400 transition-all font-mono font-bold text-xs uppercase tracking-wider flex items-center gap-2"
                    style={{ boxShadow: "0 0 15px rgba(234, 179, 8, 0.5)" }}
                >
                    <AlertTriangle className="w-4 h-4" />
                    <span>REVIEW NEEDED</span>
                </motion.button>
            )}
        </div>
    );
}
