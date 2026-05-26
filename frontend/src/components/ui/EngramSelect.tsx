import * as Select from '@radix-ui/react-select';

interface EngramSelectOption {
    value: string;
    label: string;
}

interface EngramSelectProps {
    id?: string;
    value: string;
    onValueChange: (value: string) => void;
    options: EngramSelectOption[];
    disabled?: boolean;
}

export function EngramSelect({ id, value, onValueChange, options, disabled }: EngramSelectProps) {
    return (
        <Select.Root value={value} onValueChange={onValueChange} disabled={disabled}>
            <Select.Trigger id={id} className="sv-select-trigger">
                <Select.Value />
                <Select.Icon className="sv-select-icon">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true">
                        <path d="M6 9l6 6 6-6" />
                    </svg>
                </Select.Icon>
            </Select.Trigger>
            <Select.Portal>
                <Select.Content className="sv-select-content" position="popper" sideOffset={4}>
                    <Select.ScrollUpButton className="sv-select-scroll-btn">▴</Select.ScrollUpButton>
                    <Select.Viewport>
                        {options.map((opt) => (
                            <Select.Item key={opt.value} value={opt.value} className="sv-select-item">
                                <Select.ItemText>{opt.label}</Select.ItemText>
                                <Select.ItemIndicator className="sv-select-indicator">✓</Select.ItemIndicator>
                            </Select.Item>
                        ))}
                    </Select.Viewport>
                    <Select.ScrollDownButton className="sv-select-scroll-btn">▾</Select.ScrollDownButton>
                </Select.Content>
            </Select.Portal>
        </Select.Root>
    );
}
