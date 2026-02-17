/**
 * Edition tag input component for movie titles
 */

interface EditionInputProps {
    titleId: number;
    value: string;
    onChange: (titleId: number, edition: string) => void;
}

export function EditionInput({ titleId, value, onChange }: EditionInputProps) {
    return (
        <div className="col-edition">
            <input
                type="text"
                placeholder="e.g. Extended, Director's Cut"
                list="edition-suggestions"
                value={value}
                onChange={(e) => onChange(titleId, e.target.value)}
                className="edition-input"
            />
            <datalist id="edition-suggestions">
                <option value="Theatrical" />
                <option value="Extended" />
                <option value="Director's Cut" />
                <option value="Unrated" />
                <option value="IMAX" />
            </datalist>
        </div>
    );
}
