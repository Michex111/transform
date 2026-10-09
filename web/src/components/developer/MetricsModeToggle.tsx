/**
 * The Static / Live segmented control.
 *
 * A radio group rather than a switch: "Live" is a *mode* the chart is in, and the
 * two options are labelled and visible at once — which is what makes the current
 * mode unambiguous. A bare toggle would leave the user to infer whether the
 * highlight means "on" or "the thing you would switch to".
 *
 * The live state (Connecting / Live / Reconnecting / Disconnected) is reported
 * separately by `LiveIndicator`; this control only owns the *intent*.
 */
export function MetricsModeToggle({
  live,
  onChange,
  disabled = false,
}: {
  live: boolean;
  onChange: (live: boolean) => void;
  disabled?: boolean;
}) {
  const options = [
    { value: false, label: "Static", hint: "Load a snapshot; refresh on demand" },
    { value: true, label: "Live", hint: "Stream new aggregates automatically" },
  ];

  return (
    <div
      role="radiogroup"
      aria-label="Chart update mode"
      className="inline-flex rounded-lg border border-outline bg-surface p-0.5"
      onKeyDown={(event) => {
        // A radiogroup is expected to be navigable with the arrow keys. Without
        // this the roles promise an interaction that does not exist, which is
        // worse for a screen-reader user than plain buttons would have been.
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          event.preventDefault();
          onChange(true);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          event.preventDefault();
          onChange(false);
        }
      }}
    >
      {options.map((option) => {
        const selected = option.value === live;
        return (
          <button
            key={option.label}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={disabled}
            // Roving tabindex: the group is one stop in the tab order and the
            // arrow keys move within it, which is the expected radiogroup
            // interaction.
            tabIndex={selected ? 0 : -1}
            title={option.hint}
            onClick={() => onChange(option.value)}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              selected
                ? option.value
                  ? "bg-success-container text-on-success-container"
                  : "bg-primary-container text-on-primary-container"
                : "text-muted hover:bg-surface-variant hover:text-on-background"
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
