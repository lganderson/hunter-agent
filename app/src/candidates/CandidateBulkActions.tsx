import { useEffect, useRef } from "react";

export type CandidateBulkAction = {
  id: string;
  label: string;
  disabled?: boolean;
  primary?: boolean;
  title?: string;
  run: () => void;
};

export function CandidateSelectionCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  label,
  onChange
}: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (inputRef.current) inputRef.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      ref={inputRef}
      className="candidate-selection-checkbox"
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-label={label}
      onChange={event => onChange(event.target.checked)}
    />
  );
}

export function CandidateBulkActions({
  selectedCount,
  shownCount,
  pending,
  actions,
  clear
}: {
  selectedCount: number;
  shownCount: number;
  pending: boolean;
  actions: CandidateBulkAction[];
  clear: () => void;
}) {
  return (
    <div className="candidate-selection-bar" role="region" aria-label="Bulk candidate actions">
      <div className="candidate-selection-count">
        <strong>{selectedCount}</strong>
        <span>selected from {shownCount} shown</span>
      </div>
      <div className="candidate-selection-actions">
        {actions.map(action => (
          <button
            className={action.primary ? "button compact primary" : "button compact"}
            key={action.id}
            type="button"
            disabled={pending || action.disabled}
            title={action.title}
            onClick={action.run}
          >
            {action.label}
          </button>
        ))}
        <button className="button compact" type="button" disabled={pending} onClick={clear}>
          Clear selection
        </button>
      </div>
    </div>
  );
}
