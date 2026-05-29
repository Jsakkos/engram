import { useState } from 'react';
import { sv } from '../../app/components/synapse';
import type { OrderingOption } from './types';

/**
 * Per-show episode-ordering picker (#200). Only rendered when the show has a
 * divergent alternative ordering (DVD/digital/...) — i.e. the common
 * single-ordering case never sees it. Selecting an ordering persists it for the
 * whole show and reorganizes future rips; the canonical (aired) identity used
 * for matching, history, and the fingerprint network is unaffected.
 */
export function OrderingSelector({
    options,
    current,
    onChange,
}: {
    options: OrderingOption[];
    current: string;
    onChange: (ordering: string) => void | Promise<void>;
}) {
    const [busy, setBusy] = useState(false);

    const handle = async (next: string) => {
        if (next === current || busy) return;
        setBusy(true);
        try {
            await onChange(next);
        } finally {
            setBusy(false);
        }
    };

    const activeDiverges = options.find((o) => o.ordering === current)?.diverges ?? false;

    return (
        <div
            style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                flexWrap: 'wrap',
                padding: '10px 12px',
                background: sv.bg0,
                border: `1px solid ${sv.lineMid}`,
            }}
        >
            <span
                style={{
                    fontFamily: sv.mono,
                    fontSize: 10,
                    letterSpacing: '0.14em',
                    textTransform: 'uppercase',
                    color: sv.inkFaint,
                }}
            >
                Episode ordering
            </span>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                {options.map((opt) => {
                    const selected = opt.ordering === current;
                    return (
                        <button
                            key={opt.ordering}
                            type="button"
                            disabled={busy}
                            onClick={() => handle(opt.ordering)}
                            title={
                                opt.diverges
                                    ? `${opt.label} renumbers some episodes on this disc`
                                    : opt.label
                            }
                            style={{
                                fontFamily: sv.mono,
                                fontSize: 11,
                                letterSpacing: '0.04em',
                                padding: '5px 10px',
                                cursor: busy ? 'wait' : 'pointer',
                                background: selected ? sv.cyan : 'transparent',
                                color: selected ? sv.bg0 : sv.inkDim,
                                border: `1px solid ${selected ? sv.cyan : sv.lineMid}`,
                                fontWeight: selected ? 700 : 400,
                            }}
                        >
                            {opt.label}
                            {opt.diverges && !selected ? ' •' : ''}
                        </button>
                    );
                })}
            </div>
            <span style={{ fontFamily: sv.sans, fontSize: 10, color: sv.inkFaint }}>
                {activeDiverges
                    ? 'Files use this ordering; matching & history stay canonical.'
                    : 'Aired order — matches the canonical numbering.'}
            </span>
        </div>
    );
}
